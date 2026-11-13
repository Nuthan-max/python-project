"""
Delhi road-network acquisition and export.

Responsibilities
----------------
1. Download the real OpenStreetMap drivable network for Delhi NCT via OSMnx
   and cache it locally as GraphML.
2. Augment edges with travel-time attributes computed from a documented
   speed model (see config.HIGHWAY_SPEEDS_KPH).
3. Export a compact GeoJSON FeatureCollection of the road network so Leaflet
   can draw the *real* street layout without touching OSMnx.

Run directly to (re)build the cache:
    python -m core.network
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import networkx as nx

import config

try:  # OSMnx is only required for download/refresh, not for serving the app
    import osmnx as ox
except Exception:  # pragma: no cover - dependency guard
    ox = None


# ---------------------------------------------------------------------------
# Speed / time model
# ---------------------------------------------------------------------------
def estimate_speed_kph(edge_data: Dict[str, Any]) -> float:
    """Free-flow speed estimate for one directed edge (km/h).

    Priority: OSM `maxspeed` tag -> highway-class table -> DEFAULT_SPEED_KPH.
    Values are estimates, not measured Delhi traffic.
    """
    if getattr(config, "USE_OSM_MAXSPEED", False):
        maxspeed = edge_data.get("maxspeed")
        if maxspeed:
            value = _parse_maxspeed(str(maxspeed))
            if value:
                return value

    highway = edge_data.get("highway")
    if isinstance(highway, list):
        highway = highway[0] if highway else None
    if highway:
        speed = config.HIGHWAY_SPEEDS_KPH.get(str(highway))
        if speed:
            return speed
    return config.DEFAULT_SPEED_KPH


def _parse_maxspeed(raw: str) -> Optional[float]:
    try:
        cleaned = raw.strip().lower().split(";")[0].replace("km/h", "").strip()
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def normalize_node_ids(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """GraphML stores node ids as ints; make them strings for consistent JSON."""
    if all(isinstance(n, str) for n in G.nodes()):
        return G
    return nx.relabel_nodes(G, {n: str(n) for n in G.nodes()}, copy=True)


def annotate_graph(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Add speed_kph / free_time_s / length_m attributes to every edge."""
    G = normalize_node_ids(G)
    for u, v, k, data in G.edges(keys=True, data=True):
        length = float(data.get("length") or 0.0)
        speed = estimate_speed_kph(data)
        data["length_m"] = length
        data["speed_kph"] = speed
        data["free_time_s"] = (length / (speed * 1000.0 / 3600.0)) if speed > 0 else 0.0
        data["name"] = _first(data.get("name")) or ""
        data["highway"] = _first(data.get("highway")) or ""
        data["oneway"] = bool(data.get("oneway", False))
    G.graph["speed_model"] = "estimated-by-road-class"
    G.graph["source"] = "OpenStreetMap via OSMnx"
    return G


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


# ---------------------------------------------------------------------------
# Download / cache
# ---------------------------------------------------------------------------
def download_delhi_graph(force: bool = False) -> nx.MultiDiGraph:
    """Return the cached Delhi graph, downloading it if necessary."""
    if ox is None:
        raise RuntimeError(
            "OSMnx is not installed. Run: pip install osmnx  "
            "(or supply data/cache/delhi_drive.graphml manually)."
        )

    if config.GRAPH_PATH.exists() and not force:
        print(f"[network] using cached graph: {config.GRAPH_PATH}")
        G = load_graph()
        return annotate_graph(G)

    ox.settings.use_cache = config.OSMNX_SETTINGS["use_cache"]
    ox.settings.log_console = config.OSMNX_SETTINGS["log_console"]
    ox.settings.requests_timeout = config.OSMNX_SETTINGS["requests_timeout"]

    print(f"[network] downloading OSM drive network for: {config.PLACE_QUERIES}")
    started = time.time()
    G = ox.graph_from_place(
        config.PLACE_QUERIES,
        network_type=config.NETWORK_TYPE,
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )
    # Largest strongly connected component: guarantees "ambulance can always
    # drive back" (one-way systems otherwise create dead-end components).
    G = ox.truncate.largest_component(G, strongly=True)

    G = annotate_graph(G)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, filepath=config.GRAPH_PATH)

    meta = {
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "area": config.AREA_NAME,
        "queries": config.PLACE_QUERIES,
        "network_type": config.NETWORK_TYPE,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "download_seconds": round(time.time() - started, 1),
        "source": "OpenStreetMap via OSMnx",
    }
    config.GRAPH_META_PATH.write_text(json.dumps(meta, indent=2))
    print(f"[network] saved {meta['nodes']} nodes / {meta['edges']} edges "
          f"in {meta['download_seconds']}s")
    return G


def load_graph(path=None) -> nx.MultiDiGraph:
    if ox is None:
        raise RuntimeError("OSMnx is not installed; cannot read GraphML.")
    return normalize_node_ids(ox.load_graphml(path or config.GRAPH_PATH))


# ---------------------------------------------------------------------------
# GeoJSON export for Leaflet
# ---------------------------------------------------------------------------
def _edge_coords(G: nx.MultiDiGraph, u: str, v: str, data: Dict[str, Any]):
    """Return [[lat, lng], ...] for an edge using its geometry when present."""
    if "geometry" in data and data["geometry"] is not None:
        geom = data["geometry"]
        try:
            return [[lat, lng] for lng, lat in geom.coords]
        except Exception:
            pass
    a, b = G.nodes[u], G.nodes[v]
    return [[a["y"], a["x"]], [b["y"], b["x"]]]


def export_network_geojson(
    G: nx.MultiDiGraph,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Dict[str, Any]:
    """Undirected-merged FeatureCollection of every drivable link.

    One feature per unique street segment (u<v key) to keep payload small.
    Each feature carries a `ref` (u,v,key) so the frontend can map a
    clicked segment back to concrete edges for incident creation. If bbox is
    provided, only segments whose geometry intersects it are exported.
    """
    features = []
    seen = set()
    if bbox is not None:
        west, south, east, north = bbox
    for u, v, k, data in G.edges(keys=True, data=True):
        pair = frozenset((u, v))
        # For one-way segments the (u,v) pair is unique; for two-way we merge
        if pair in seen:
            continue
        if bbox is not None:
            geom = data.get("geometry")
            if geom is not None:
                min_lng, min_lat, max_lng, max_lat = geom.bounds
            else:
                start, end = G.nodes[u], G.nodes[v]
                min_lng, max_lng = sorted((start["x"], end["x"]))
                min_lat, max_lat = sorted((start["y"], end["y"]))
            if (max_lng < west or min_lng > east
                    or max_lat < south or min_lat > north):
                continue
        coords = _edge_coords(G, u, v, data)
        if len(coords) < 2:
            continue
        seen.add(pair)
        features.append({
            "type": "Feature",
            "id": f"{u}_{v}",
            "properties": {
                "u": str(u), "v": str(v), "key": str(k),
                "name": data.get("name", ""),
                "highway": data.get("highway", ""),
                "length_m": round(float(data.get("length_m", 0)), 1),
                "speed_kph": round(float(data.get("speed_kph", 0)), 1),
                "oneway": bool(data.get("oneway", False)),
            },
            "geometry": {"type": "LineString", "coordinates": [[c[1], c[0]] for c in coords]},
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "OpenStreetMap via OSMnx",
            "area": config.AREA_NAME,
            "disclaimer": "Road geometry is real. Traffic, incidents and vehicles are simulated.",
        },
    }


def export_nodes_geojson(G: nx.MultiDiGraph) -> Dict[str, Any]:
    """Intersection points — used for snapping clicks in the UI."""
    features = [{
        "type": "Feature",
        "id": str(n),
        "properties": {"node_id": str(n)},
        "geometry": {"type": "Point", "coordinates": [d["x"], d["y"]]},
    } for n, d in G.nodes(data=True)]
    return {"type": "FeatureCollection", "features": features}


if __name__ == "__main__":
    graph = download_delhi_graph(force=True)
    out = config.CACHE_DIR / "network.geojson"
    out.write_text(json.dumps(export_network_geojson(graph), separators=(",", ":")))
    print(f"[network] wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
