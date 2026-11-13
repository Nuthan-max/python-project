"""
Route Engine — weighted shortest path over the real Delhi road graph.

Cost function (seconds):
    cost(edge) = free_time_s * emergency_factor * traffic_multiplier(edge)
                 -> infinite when the street is closed

Both Dijkstra and A* optimise exactly that cost, so their resulting routes
are comparable and their search effort (nodes expanded, CPU time) is the
interesting difference. A* uses haversine distance / max plausible speed as
an admissible, consistent heuristic.
"""

from __future__ import annotations

import heapq
import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx

import config

try:
    import osmnx as ox
except Exception:  # pragma: no cover
    ox = None
from core.traffic import TrafficEngine

EARTH_R = 6_371_000.0
MAX_SPEED_KPH = max(config.HIGHWAY_SPEEDS_KPH.values()) * config.EMERGENCY_SPEED_FACTOR


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


@dataclass
class RouteResult:
    algorithm: str
    node_path: List[str]
    edge_path: List[Tuple[str, str, str]]
    total_time_s: float
    total_distance_m: float
    street_time_s: float
    nodes_expanded: int
    compute_ms: float
    coordinates: List[List[float]] = field(default_factory=list)  # [[lat, lng], ...]
    steps: List[Dict[str, Any]] = field(default_factory=list)
    edge_ends: List[int] = field(default_factory=list)  # coords index per edge end

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "node_path": self.node_path,
            "total_time_s": round(self.total_time_s, 1),
            "total_distance_m": round(self.total_distance_m, 1),
            "street_time_s": round(self.street_time_s, 1),
            "nodes_expanded": self.nodes_expanded,
            "compute_ms": round(self.compute_ms, 3),
            "coordinates": self.coordinates,
            "steps": self.steps,
            "segments": len(self.edge_path),
        }


class RouteEngine:
    def __init__(self, G: nx.MultiDiGraph, traffic: TrafficEngine) -> None:
        self.G = G
        self.traffic = traffic
        self._coords = {n: (d["y"], d["x"]) for n, d in G.nodes(data=True)}

    # ------------------------------------------------------------------
    # Geometry / snapping
    # ------------------------------------------------------------------
    def nearest_node(self, lat: float, lng: float) -> str:
        """Snap a coordinate to the closest intersection in the road graph."""
        if ox is not None:
            try:
                return str(ox.distance.nearest_nodes(self.G, X=lng, Y=lat))
            except Exception:
                pass  # scikit-learn not installed -> plain geodesic scan below
        best, best_d = None, float("inf")
        for n, (nlat, nlng) in self._coords.items():
            d = haversine_m(lat, lng, nlat, nlng)
            if d < best_d:
                best, best_d = n, d
        return str(best)

    def nearest_nodes(self, points: Sequence[Tuple[float, float]]) -> List[str]:
        """Snap several (latitude, longitude) points using one spatial index."""
        if not points:
            return []
        if ox is not None:
            try:
                nodes = ox.distance.nearest_nodes(
                    self.G,
                    X=[lng for _, lng in points],
                    Y=[lat for lat, _ in points],
                )
                return [str(node) for node in nodes]
            except ImportError:
                pass
        return [self.nearest_node(lat, lng) for lat, lng in points]

    def node_xy(self, n: str) -> Tuple[float, float]:
        return self._coords[n]

    def _split_at_progress(self, coords: List[List[float]], distance_remaining_m: float):
        """Walk forward along coords until `distance_remaining_m` is consumed."""
        if not coords:
            return [0.0, 0.0], 0
        if distance_remaining_m <= 0:
            return list(coords[0]), 0
        travelled = 0.0
        for i in range(len(coords) - 1):
            a, b = coords[i], coords[i + 1]
            seg = haversine_m(a[0], a[1], b[0], b[1])
            if travelled + seg >= distance_remaining_m:
                frac = (distance_remaining_m - travelled) / seg if seg else 0.0
                return [a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac], i
            travelled += seg
        return list(coords[-1]), len(coords) - 1

    def position_along(self, coords: List[List[float]], distance_from_start_m: float):
        """Return (lat, lng, segment_index) for a position along a polyline."""
        if not coords:
            return None
        total = sum(haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
                    for i in range(len(coords) - 1))
        remaining = max(0.0, total - max(0.0, distance_from_start_m))
        return self._split_at_progress(coords, remaining)

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------
    def _edge_cost(self, u: str, v: str, data: Dict) -> float:
        factor = self.traffic.factor_for_edge(u, v, data.get("_edge_key", ""))
        if factor == float("inf"):
            return math.inf
        return data["free_time_s"] * config.EMERGENCY_SPEED_FACTOR * factor

    def edge_times(self, edge_path: List[Tuple[str, str, str]]) -> List[float]:
        """Travel time in seconds for each edge, in order."""
        times = []
        for u, v, k in edge_path:
            data = self.G[u][v][k]
            factor = self.traffic.factor_for_edge(u, v, k)
            if factor == math.inf:
                factor = 1.0  # the vehicle would not be driving here
            times.append(data["free_time_s"] * config.EMERGENCY_SPEED_FACTOR * factor)
        return times

    def route(self, source: str, target: str, algorithm: str = "astar") -> RouteResult:
        source, target = str(source), str(target)
        if source not in self.G or target not in self.G:
            raise KeyError("source or target node is not in the road network")
        started = time.perf_counter()
        best: Dict[str, float] = {source: 0.0}
        parent: Dict[str, Tuple[str, str]] = {}   # node -> (prev_node, edge_key)
        expanded = 0
        counter = itertools.count()
        tx, ty = self._coords[target]
        if algorithm == "astar":
            def h(n):
                ny, nx_ = self._coords[n]
                return haversine_m(ny, nx_, tx, ty) / (MAX_SPEED_KPH / 3.6)
        else:
            def h(n):
                return 0.0

        heap = [(h(source), next(counter), source)]
        closed = set()
        found = False
        G = self.G
        while heap:
            _, _, u = heapq.heappop(heap)
            if u in closed:
                continue
            closed.add(u)
            expanded += 1
            if u == target:
                found = True
                break
            base = best[u]
            for _u, v, key in G.out_edges(u, keys=True):
                c = self._edge_cost(u, v, G[_u][v][key])
                if c == math.inf:
                    continue
                nd = base + c
                if nd < best.get(v, math.inf):
                    best[v] = nd
                    parent[v] = (u, key)
                    heapq.heappush(heap, (nd + h(v), next(counter), v))

        compute_ms = (time.perf_counter() - started) * 1000.0
        if not found:
            raise NoRouteError(
                "No drivable route exists between these points with the current "
                "closures. Remove a closure or pick another destination."
            )

        node_path = [target]
        edge_path: List[Tuple[str, str, str]] = []
        while node_path[-1] != source:
            prev, key = parent[node_path[-1]]
            edge_path.append((prev, node_path[-1], key))
            node_path.append(prev)
        node_path.reverse()
        edge_path.reverse()

        coords: List[List[float]] = []
        steps: List[Dict[str, Any]] = []
        edge_ends: List[int] = []   # index in `coords` where each edge finishes
        total_dist = 0.0
        street_time = 0.0
        for u, v, k in edge_path:
            data = G[u][v][k]
            geom = data.get("geometry")
            if geom is not None:
                pts = [[lat, lng] for lng, lat in geom.coords]
                # orient geometry from u to v (OSM direction is not guaranteed)
                uxy = self._coords[u]
                if haversine_m(pts[0][0], pts[0][1], *uxy) > haversine_m(pts[-1][0], pts[-1][1], *uxy):
                    pts = pts[::-1]
            else:
                pts = [list(self._coords[u]), list(self._coords[v])]
            if not coords:
                coords.extend(pts)
            else:
                coords.extend(pts[1:])
            edge_ends.append(len(coords) - 1)

            traffic_factor = self.traffic.factor_for_edge(u, v, k)
            edge_time = data["free_time_s"] * config.EMERGENCY_SPEED_FACTOR * traffic_factor
            total_dist += data.get("length_m", 0.0)
            street_time += edge_time
            steps.append({
                "from": [str(u), self._coords[u][0], self._coords[u][1]],
                "to": [str(v), self._coords[v][0], self._coords[v][1]],
                "street": data.get("name") or data.get("highway") or "unnamed road",
                "highway": data.get("highway", ""),
                "distance_m": round(float(data.get("length_m", 0.0)), 1),
                "time_s": round(edge_time, 1),
                "traffic": "closed" if traffic_factor == math.inf else _factor_label(traffic_factor),
                "factor": None if traffic_factor == math.inf else round(traffic_factor, 2),
            })

        return RouteResult(
            algorithm=algorithm,
            node_path=[str(n) for n in node_path],
            edge_path=edge_path,
            total_time_s=street_time,
            total_distance_m=total_dist,
            street_time_s=street_time,
            nodes_expanded=expanded,
            compute_ms=compute_ms,
            coordinates=coords,
            steps=steps,
            edge_ends=edge_ends,
        )

    # ------------------------------------------------------------------
    # Comparison harness
    # ------------------------------------------------------------------
    def compare(self, source: str, target: str, runs: Optional[int] = None) -> Dict[str, Any]:
        runs = runs or config.BENCHMARK_RUNS
        results: Dict[str, List[RouteResult]] = {"dijkstra": [], "astar": []}
        for _ in range(runs):
            for algo in ("dijkstra", "astar"):
                results[algo].append(self.route(source, target, algo))

        summary = {}
        for algo, items in results.items():
            summary[algo] = {
                "iterations": len(items),
                "avg_compute_ms": round(sum(r.compute_ms for r in items) / len(items), 3),
                "avg_nodes_expanded": round(sum(r.nodes_expanded for r in items) / len(items), 1),
                "total_time_s": round(items[-1].total_time_s, 1),
                "total_distance_m": round(items[-1].total_distance_m, 1),
                "segment_count": len(items[-1].edge_path),
            }
        same_route = results["dijkstra"][-1].node_path == results["astar"][-1].node_path
        summary["same_optimal_route"] = same_route
        summary["speedup_x"] = round(
            summary["dijkstra"]["avg_compute_ms"] / max(summary["astar"]["avg_compute_ms"], 1e-6), 2)
        summary["astar_route"] = results["astar"][-1].to_dict()
        summary["dijkstra_route"] = results["dijkstra"][-1].to_dict()
        return summary


class NoRouteError(Exception):
    """Raised when no connected path exists under current conditions."""





def _factor_label(factor: float) -> str:
    if factor <= 1.0:
        return "clear"
    if factor <= 1.5:
        return "moderate"
    if factor <= 2.5:
        return "heavy"
    return "jam"
