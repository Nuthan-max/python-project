"""
Traffic / incident state layered on top of the real road network.

The road graph itself is REAL (OpenStreetMap). Everything in this module is
SIMULATED: congestion levels, accidents and closures that the operator
creates in the Traffic Simulator / Incident Manager.

Data model
----------
* Congestion is stored per *street segment* using an undirected segment key
  (frozenset of the two endpoint ids) so that blocking "Connaught Place ring"
  blocks both directions, which is what a real road closure does.
* Each affected segment lists the directed edges it covers.
* Routing costs are derived on demand:  cost = free_time_s * factor
  where factor comes from the worst congestion level on the segment, or a
  hard block when the segment is closed.
"""

from __future__ import annotations

import itertools
import math
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple

import requests
import config

EdgeKey = Tuple[str, str, str]  # (u, v, key)
SegmentKey = str                 # "u|v" with u<v

_object_counter = itertools.count(1)


def segment_key(u: str, v: str) -> SegmentKey:
    a, b = sorted((str(u), str(v)))
    return f"{a}|{b}"


@dataclass
class TrafficStateEntry:
    segment: SegmentKey
    level: str                 # clear | moderate | heavy | jam | closed
    reason: str = ""
    incident_id: Optional[int] = None
    source: str = "simulated"  # "simulated" | "tomtom_live" | "osm_live"
    updated_at: float = field(default_factory=time.time)

    @property
    def multiplier(self) -> float:
        if self.level == "closed":
            return float("inf")
        return config.CONGESTION_MULTIPLIERS.get(self.level, 1.0)


@dataclass
class Incident:
    id: int
    kind: str                  # accident | closure | traffic
    segment: SegmentKey
    edges: List[EdgeKey]
    level: str
    label: str
    lat: float
    lng: float
    created_at: float
    active: bool = True
    source: str = "simulated"  # "simulated" | "tomtom_live"

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["created_at_iso"] = time.strftime("%H:%M:%S", time.localtime(self.created_at))
        return d


class TrafficEngine:
    """Thread-safe, in-memory network condition store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: Dict[SegmentKey, TrafficStateEntry] = {}
        self._incidents: Dict[int, Incident] = {}
        self._segment_edges: Dict[SegmentKey, List[EdgeKey]] = {}
        self._segment_keys: List[SegmentKey] = []
        self._node_segments: Dict[str, List[SegmentKey]] = {}
        self._auto_started = False
        self._last_live_sync: float = 0.0
        self._live_traffic_status = {
            "enabled": bool(config.TOMTOM_API_KEY),
            "provider": "TomTom Traffic Incidents" if config.TOMTOM_API_KEY else "Simulated Generator",
            "last_sync": None,
            "live_count": 0,
            "error": None
        }

    # -- registration -------------------------------------------------------
    def register_graph_edges(self, edges: Iterable[Tuple[str, str, str]]) -> None:
        with self._lock:
            self._segment_edges.clear()
            self._node_segments.clear()
            for u, v, k in edges:
                u, v = str(u), str(v)
                segment = segment_key(u, v)
                is_new_segment = segment not in self._segment_edges
                self._segment_edges.setdefault(segment, []).append((u, v, str(k)))
                if is_new_segment:
                    self._node_segments.setdefault(u, []).append(segment)
                    self._node_segments.setdefault(v, []).append(segment)
            self._segment_keys = list(self._segment_edges)

    def segment_count(self) -> int:
        return len(self._segment_edges)

    def edges_for_segment(self, seg: SegmentKey) -> List[EdgeKey]:
        return self._segment_edges.get(seg, [])

    # -- condition access ---------------------------------------------------
    def factor_for_edge(self, u: str, v: str, k: str = "") -> float:
        """Multiplier for one directed edge (inf if the street is closed)."""
        with self._lock:
            state = self._states.get(segment_key(u, v))
            return state.multiplier if state else 1.0

    def is_blocked(self, u: str, v: str) -> bool:
        return self.factor_for_edge(u, v) == float("inf")

    def state_for_segment(self, seg: SegmentKey) -> Optional[TrafficStateEntry]:
        return self._states.get(seg)

    def snapshot(self) -> Dict[str, Dict]:
        with self._lock:
            return {
                seg: {
                    "level": s.level, "reason": s.reason,
                    "incident_id": s.incident_id,
                    "source": s.source,
                    "multiplier": None if s.multiplier == float("inf") else s.multiplier,
                }
                for seg, s in self._states.items()
            }

    def segments(self) -> List[str]:
        return list(self._segment_edges.keys())

    def get_status(self) -> Dict:
        with self._lock:
            st = dict(self._live_traffic_status)
            st["active_states"] = len(self._states)
            st["incidents"] = len([i for i in self._incidents.values() if i.active])
            return st

    # -- mutation -----------------------------------------------------------
    def set_traffic(self, seg: SegmentKey, level: str, reason: str = "",
                    incident_id: Optional[int] = None,
                    source: str = "simulated") -> Optional[TrafficStateEntry]:
        if level not in config.CONGESTION_MULTIPLIERS and level != "closed":
            raise ValueError(f"unknown traffic level: {level}")
        if seg not in self._segment_edges:
            raise KeyError(f"unknown segment: {seg}")
        with self._lock:
            entry = TrafficStateEntry(seg, level, reason, incident_id, source=source)
            if level == "clear":
                self._states.pop(seg, None)
                return None
            self._states[seg] = entry
            return entry

    def clear_segment(self, seg: SegmentKey) -> None:
        with self._lock:
            self._states.pop(seg, None)

    def clear_all(self) -> None:
        with self._lock:
            self._states.clear()
            self._incidents.clear()

    # -- incidents ----------------------------------------------------------
    def add_incident(self, seg: SegmentKey, kind: str, level: str, label: str,
                     lat: float, lng: float, source: str = "simulated") -> Incident:
        if seg not in self._segment_edges:
            raise KeyError(f"unknown segment: {seg}")
        with self._lock:
            incident = Incident(
                id=next(_object_counter), kind=kind, segment=seg,
                edges=self._segment_edges[seg], level=level, label=label,
                lat=lat, lng=lng, created_at=time.time(), source=source,
            )
            self._incidents[incident.id] = incident
            self._states[seg] = TrafficStateEntry(seg, level, label, incident.id, source=source)
            return incident

    def sync_live_traffic(self, engine=None) -> Dict:
        """
        Polls TomTom Live Traffic Incidents API for Delhi NCT (Free Tier).
        Cached to avoid consuming daily free quota.
        """
        now = time.time()
        if not config.TOMTOM_API_KEY:
            self._live_traffic_status["enabled"] = False
            self._live_traffic_status["error"] = "No TOMTOM_API_KEY set; running in simulated mode"
            return self._live_traffic_status

        # Cache check
        if now - self._last_live_sync < config.LIVE_TRAFFIC_CACHE_TTL_S:
            return self._live_traffic_status

        self._last_live_sync = now
        try:
            # TomTom Incident Details v5: bbox format: minLon,minLat,maxLon,maxLat
            min_lon, min_lat, max_lon, max_lat = config.DELHI_BBOX
            url = (
                f"https://api.tomtom.com/traffic/services/5/incidentDetails"
                f"?key={config.TOMTOM_API_KEY}&bbox={min_lon},{min_lat},{max_lon},{max_lat}"
                f"&fields={{incidents{{type,geometry{{type,coordinates}},properties{{iconCategory,magnitudeOfDelay,events{{description}}}}}}}}"
                f"&language=en-GB"
            )
            resp = requests.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                incidents_data = data.get("incidents", [])
                live_count = 0
                located_incidents = []
                locations = []
                for inc in incidents_data:
                    geom = inc.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if not coords:
                        continue
                    # Pick midpoint coordinate [lng, lat]
                    mid_pt = coords[0] if isinstance(coords[0], (int, float)) else coords[len(coords) // 2]
                    lng, lat = mid_pt[0], mid_pt[1]
                    located_incidents.append((inc, lat, lng))
                    locations.append((lat, lng))

                nearest_nodes = (
                    engine.nearest_nodes(locations)
                    if engine and locations else [None] * len(locations)
                )
                with self._lock:
                    for previous in self._incidents.values():
                        if not previous.active or previous.source != "tomtom_live":
                            continue
                        previous.active = False
                        state = self._states.get(previous.segment)
                        if state and state.incident_id == previous.id:
                            self._states.pop(previous.segment, None)

                for (inc, lat, lng), nearest_n in zip(located_incidents, nearest_nodes):
                    props = inc.get("properties", {})
                    icon_cat = props.get("iconCategory", 0)
                    mag = props.get("magnitudeOfDelay", 0)
                    
                    cat_map = {
                        1: ("accident", "jam", "Live Accident"),
                        6: ("traffic", "jam", "Traffic Jam"),
                        7: ("closure", "heavy", "Lane Restriction"),
                        8: ("closure", "closed", "Road Closed"),
                        9: ("closure", "heavy", "Road Works"),
                        14: ("traffic", "heavy", "Vehicle Breakdown")
                    }
                    if icon_cat in cat_map:
                        kind, level, cat_label = cat_map[icon_cat]
                        desc = f"{cat_label}"
                    else:
                        kind = "traffic"
                        level = "jam" if mag >= 3 else ("heavy" if mag == 2 else "moderate")
                        desc = "Traffic Congestion"
                    # If route engine provided, snap to nearest segment
                    if engine:
                        nearest_lat, nearest_lng = engine.node_xy(nearest_n)
                        lat1, lat2 = math.radians(lat), math.radians(nearest_lat)
                        dlat = lat2 - lat1
                        dlng = math.radians(nearest_lng - lng)
                        a = (math.sin(dlat / 2) ** 2
                             + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2)
                        snap_distance_m = 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, a)))
                        if snap_distance_m > config.TOMTOM_SNAP_MAX_DISTANCE_M:
                            continue
                        cand_seg = next(
                            (seg for seg in self._node_segments.get(str(nearest_n), [])
                             if seg not in self._states),
                            None,
                        )
                        if cand_seg:
                            self.add_incident(
                                seg=cand_seg,
                                kind=kind,
                                level=level,
                                label=f"Live TomTom: {desc}",
                                lat=lat,
                                lng=lng,
                                source="tomtom_live"
                            )
                            live_count += 1

                self._live_traffic_status["enabled"] = True
                self._live_traffic_status["live_count"] = live_count
                self._live_traffic_status["last_sync"] = time.strftime("%H:%M:%S", time.localtime(now))
                self._live_traffic_status["error"] = None
            else:
                self._live_traffic_status["error"] = f"TomTom API HTTP {resp.status_code}"
        except Exception as ex:
            self._live_traffic_status["error"] = (
                f"TomTom request failed ({type(ex).__name__}); "
                "check server configuration and network access."
            )

        return self._live_traffic_status

    def resolve_incident(self, incident_id: int) -> bool:
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                return False
            inc.active = False
            state = self._states.get(inc.segment)
            if state and state.incident_id == incident_id:
                self._states.pop(inc.segment, None)
            return True

    def incidents(self, active_only: bool = True) -> List[Dict]:
        with self._lock:
            items = [i.to_dict() for i in self._incidents.values()
                     if i.active or not active_only]
        return sorted(items, key=lambda d: d["created_at"])

    # -- background auto traffic (demo realism) -----------------------------
    def start_auto_traffic(self) -> None:
        if self._auto_started or not config.AUTO_TRAFFIC_EVENTS:
            return
        self._auto_started = True

        def loop():
            while True:
                time.sleep(config.AUTO_TRAFFIC_INTERVAL_S)
                try:
                    self._random_congestion()
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True, name="auto-traffic").start()

    def _random_congestion(self) -> None:
        """Recover one jammed street and congest another — purely simulated."""
        if not self._segment_keys:
            return
        with self._lock:
            for seg, state in list(self._states.items()):
                if state.incident_id is None and state.level in ("heavy", "jam") and random.random() < 0.6:
                    self._states.pop(seg, None)
            for _ in range(min(32, len(self._segment_keys))):
                seg = random.choice(self._segment_keys)
                if seg not in self._states:
                    level = random.choice(["moderate", "heavy"])
                    self._states[seg] = TrafficStateEntry(
                        seg, level, "Simulated background traffic"
                    )
                    break


# Module-level singleton used by the Flask app
traffic_engine = TrafficEngine()
