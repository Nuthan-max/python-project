"""
Mission simulation — moving ambulance, disruption handling and rerouting.

A mission follows the real demo story:
  1. assigned   : route computed from ambulance -> emergency -> hospital
  2. en_route_pickup : vehicle moves to the emergency
  3. on_scene   : brief stop to "collect" the patient
  4. en_route_hospital : vehicle moves to the hospital
  5. completed  : report produced

Every tick the vehicle advances by distance actually covered. If the street
it is currently on becomes closed, it is treated as a disruption and the
engine recalculates a fresh route from the vehicle's current position.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import config
from core.routing import RouteEngine, RouteResult, haversine_m
from core.traffic import TrafficEngine, segment_key

PICKUP_DWELL_S = 6.0          # simulated seconds on scene
OFF_ROUTE_TOLERANCE_M = 40.0  # vehicle must be this far along before rerouting


@dataclass
class Leg:
    name: str
    result: RouteResult
    distance_covered_m: float = 0.0
    started_at: float = field(default_factory=time.time)
    edge_index: int = 0
    edge_progress_m: float = 0.0
    edge_times: List[float] = field(default_factory=list)

    @property
    def remaining_m(self) -> float:
        return max(0.0, _polyline_length(self.result.coordinates) - self.distance_covered_m)

    @property
    def at_end(self) -> bool:
        return self.edge_index >= len(self.result.edge_path)

    def edge_length(self, idx: int) -> float:
        coords, ends = self.result.coordinates, self.result.edge_ends
        if not ends:
            return _polyline_length(coords)
        start = 0 if idx == 0 else ends[idx - 1]
        end = ends[idx] if idx < len(ends) else len(coords) - 1
        return sum(haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
                   for i in range(start, end))


@dataclass
class Mission:
    id: int
    ambulance_id: str
    ambulance_label: str
    emergency: Dict[str, float]
    hospital: Dict[str, Any]
    algorithm: str
    status: str = "assigned"
    legs: List[Leg] = field(default_factory=list)
    leg_index: int = 0
    reroutes: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    dwell_until: float = 0.0
    position: Optional[List[float]] = None   # [lat, lng]
    heading: float = 0.0
    distance_travelled_m: float = 0.0
    events: List[Dict[str, Any]] = field(default_factory=list)
    original_route: Optional[Dict[str, Any]] = None
    pending_reroute_reason: Optional[str] = None
    last_tick: float = field(default_factory=time.time)
    route_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    is_live_telemetry: bool = False
    telemetry_source: str = "simulated"  # "simulated" | "real_device_gps"
    last_telemetry_time: Optional[float] = None
    gps_accuracy_m: Optional[float] = None

    # -- helpers ------------------------------------------------------------
    @property
    def current_leg(self) -> Optional[Leg]:
        if 0 <= self.leg_index < len(self.legs):
            return self.legs[self.leg_index]
        return None

    def log(self, kind: str, message: str, **extra) -> None:
        self.events.append({
            "t": round((time.time() - (self.started_at or time.time())), 1),
            "kind": kind,
            "message": message,
            **extra,
        })

    def to_dict(self, engine: Optional[RouteEngine] = None) -> Dict[str, Any]:
        leg = self.current_leg
        remaining = leg.remaining_m if leg else 0.0
        eta_s = (remaining / max(_speed_of(leg), 1.0)) if leg else 0.0
        return {
            "id": self.id,
            "status": self.status,
            "algorithm": self.algorithm,
            "ambulance_id": self.ambulance_id,
            "ambulance_label": self.ambulance_label,
            "emergency": self.emergency,
            "hospital": self.hospital,
            "position": self.position,
            "heading": self.heading,
            "is_live_telemetry": self.is_live_telemetry,
            "telemetry_source": self.telemetry_source,
            "gps_accuracy_m": self.gps_accuracy_m,
            "leg_index": self.leg_index,
            "leg_name": leg.name if leg else None,
            "legs": [
                {
                    "name": l.name,
                    "algorithm": l.result.algorithm,
                    "coordinates": l.result.coordinates,
                    "distance_m": round(_polyline_length(l.result.coordinates), 1),
                    "time_s": round(l.result.total_time_s, 1),
                    "covered_m": round(l.distance_covered_m, 1),
                    "steps": l.result.steps,
                } for l in self.legs
            ],
            "current_leg_coordinates": leg.result.coordinates if leg else [],
            "remaining_m": round(remaining, 1),
            "eta_s": round(eta_s, 1),
            "distance_travelled_m": round(self.distance_travelled_m, 1),
            "reroutes": self.reroutes,
            "original_route": self.original_route,
            "events": self.events,
            "created_at_iso": time.strftime("%H:%M:%S", time.localtime(self.created_at)),
            "elapsed_s": round((self.finished_at or time.time()) - (self.started_at or time.time()), 1)
            if self.started_at else 0.0,
        }


def _speed_of(leg: Optional[Leg]) -> float:
    """Average m/s of a leg used for a rough ETA display."""
    if not leg:
        return 0.0
    dist = _polyline_length(leg.result.coordinates)
    if dist <= 0 or leg.result.total_time_s <= 0:
        return 0.0
    return dist / leg.result.total_time_s


def _arc_speed(leg: Leg) -> float:
    """On-screen m/s of the road segment currently being driven.

    Each edge is traversed at its own simulated speed (free-flow time scaled by
    congestion, amplified by SIM_SPEED_FACTOR), so the ambulance visibly slows
    on jammed streets instead of gliding at one flat speed.
    """
    idx = leg.edge_index
    if 0 <= idx < len(leg.edge_times) and leg.edge_times[idx] > 0:
        length = leg.edge_length(idx)
        if length > 0:
            return (length / leg.edge_times[idx]) * config.SIM_SPEED_FACTOR
    base = _speed_of(leg)
    return (base or 12.0) * config.SIM_SPEED_FACTOR


def _heading(coords: List[List[float]], idx: int) -> float:
    """Bearing in degrees between consecutive points (for marker rotation)."""
    if len(coords) < 2:
        return 0.0
    i = min(idx, len(coords) - 2)
    lat1, lng1 = coords[i]
    lat2, lng2 = coords[i + 1]
    dlon = math.radians(lng2 - lng1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _polyline_length(coords: List[List[float]]) -> float:
    return sum(haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
               for i in range(len(coords) - 1))


class MissionManager:
    """Owns open missions and advances them on a background thread."""

    def __init__(self, engine: RouteEngine, traffic: TrafficEngine) -> None:
        self.engine = engine
        self.traffic = traffic
        self._missions: Dict[int, Mission] = {}
        self._lock = threading.RLock()
        self._next_id = 1
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.mission_log: List[Dict[str, Any]] = []   # finished reports

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    def create_mission(self, ambulance_id: str, ambulance_label: str,
                       ambulance_lat: float, ambulance_lng: float,
                       emergency_lat: float, emergency_lng: float,
                       hospital: Dict[str, Any], algorithm: str) -> Mission:
        start_node = self.engine.nearest_node(ambulance_lat, ambulance_lng)
        pickup_node = self.engine.nearest_node(emergency_lat, emergency_lng)
        hospital_node = self.engine.nearest_node(float(hospital["lat"]), float(hospital["lng"]))

        to_pickup = self.engine.route(start_node, pickup_node, algorithm)
        to_hospital = self.engine.route(pickup_node, hospital_node, algorithm)

        hospital = dict(hospital)
        hospital["node"] = str(hospital_node)

        with self._lock:
            mission = Mission(
                id=self._next_id,
                ambulance_id=ambulance_id,
                ambulance_label=ambulance_label,
                emergency={"lat": emergency_lat, "lng": emergency_lng,
                           "node": str(pickup_node)},
                hospital=hospital,
                algorithm=algorithm,
                legs=[self._make_leg("ambulance_to_emergency", to_pickup),
                      self._make_leg("emergency_to_hospital", to_hospital)],
                position=to_pickup.coordinates[0] if to_pickup.coordinates else None,
            )
            mission.original_route = {
                "to_pickup": to_pickup.to_dict(),
                "to_hospital": to_hospital.to_dict(),
                "estimated_total_s": round(to_pickup.total_time_s + to_hospital.total_time_s, 1),
            }
            mission.log("created", f"Mission #{mission.id} planned with {algorithm.upper()}.")
            self._missions[mission.id] = mission
            self._next_id += 1
        self._ensure_thread()
        return mission

    def _make_leg(self, name: str, result: RouteResult) -> Leg:
        return Leg(name, result, edge_times=self.engine.edge_times(result.edge_path))

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def start(self, mission_id: int) -> Mission:
        mission = self.get(mission_id)
        with mission.route_lock:
            if mission.status not in ("assigned",):
                raise ValueError(f"mission is already {mission.status}")
            mission.status = "en_route_pickup"
            mission.started_at = time.time()
            mission.last_tick = time.time()
            mission.log("started", "Ambulance dispatched to the emergency site.")
        return mission

    def pause(self, mission_id: int) -> Mission:
        mission = self.get(mission_id)
        with mission.route_lock:
            if mission.status.startswith("en_route") or mission.status == "on_scene":
                mission.status += "_paused"
                mission.log("paused", "Simulation paused.")
        return mission

    def resume(self, mission_id: int) -> Mission:
        mission = self.get(mission_id)
        with mission.route_lock:
            if mission.status.endswith("_paused"):
                mission.status = mission.status.replace("_paused", "")
                mission.last_tick = time.time()
                mission.log("resumed", "Simulation resumed.")
        return mission

    def abort(self, mission_id: int) -> Mission:
        mission = self.get(mission_id)
        with mission.route_lock:
            mission.status = "aborted"
            mission.finished_at = time.time()
            mission.log("aborted", "Mission aborted by operator.")
            self._archive(mission)
        return mission

    def get(self, mission_id: int) -> Mission:
        with self._lock:
            if mission_id not in self._missions:
                raise KeyError(f"unknown mission: {mission_id}")
            return self._missions[mission_id]

    def list_active(self) -> List[Mission]:
        with self._lock:
            return [m for m in self._missions.values() if not m.status.startswith("aborted")]

    def list_reports(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self.mission_log))

    # ------------------------------------------------------------------
    # Live GPS Telemetry from Driver Device
    # ------------------------------------------------------------------
    def update_live_telemetry(self, ambulance_id: str, lat: float, lng: float,
                              heading: Optional[float] = None, speed_mps: Optional[float] = None,
                              accuracy_m: Optional[float] = None) -> Optional[Mission]:
        """
        Receives real GPS coordinates broadcast from a driver's smartphone or external GPS.
        Updates position, snaps to mission route, and triggers auto-reroute if road is blocked.
        """
        now = time.time()
        target_mission = None
        with self._lock:
            for m in self._missions.values():
                if m.ambulance_id == ambulance_id and m.status in ("assigned", "en_route_pickup", "en_route_hospital"):
                    target_mission = m
                    break

        if not target_mission:
            return None

        with target_mission.route_lock:
            target_mission.position = [lat, lng]
            if heading is not None:
                target_mission.heading = float(heading)
            target_mission.is_live_telemetry = True
            target_mission.telemetry_source = "real_device_gps"
            target_mission.last_telemetry_time = now
            target_mission.gps_accuracy_m = accuracy_m

            # Auto-start if assigned
            if target_mission.status == "assigned":
                target_mission.status = "en_route_pickup"
                target_mission.started_at = now
                target_mission.log("gps_start", "Live GPS stream received from driver device. Mission started.")

            leg = target_mission.current_leg
            if leg and leg.result.coordinates:
                # Find closest point along route coordinates to update progress
                coords = leg.result.coordinates
                min_dist = float("inf")
                closest_idx = 0
                for i, (clat, clng) in enumerate(coords):
                    d = haversine_m(lat, lng, clat, clng)
                    if d < min_dist:
                        min_dist = d
                        closest_idx = i

                # Estimate distance covered up to closest_idx
                dist_up_to = 0.0
                for i in range(closest_idx):
                    dist_up_to += haversine_m(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
                leg.distance_covered_m = min(dist_up_to, leg.remaining_m + leg.distance_covered_m)

                # Check if reached destination within 35 metres
                dest_lat, dest_lng = (
                    (target_mission.emergency["lat"], target_mission.emergency["lng"])
                    if target_mission.leg_index == 0 else
                    (target_mission.hospital["lat"], target_mission.hospital["lng"])
                )
                if haversine_m(lat, lng, dest_lat, dest_lng) <= 40.0:
                    if target_mission.leg_index == 0:
                        target_mission.status = "on_scene"
                        target_mission.dwell_until = now + 4.0
                        target_mission.log("arrived", "Live GPS shows ambulance arrived at emergency scene.")
                    else:
                        target_mission.status = "completed"
                        target_mission.finished_at = now
                        target_mission.log("completed", f"Patient delivered to {target_mission.hospital['name']}.")
                        self._archive(target_mission)

                # Check if route ahead has blockage and reroute
                if self._blocking_edge(leg):
                    target_mission.log("disruption", "Live road blockage detected ahead; rerouting ambulance.")
                    self._reroute(target_mission, "Live road blockage")

        return target_mission

    # ------------------------------------------------------------------
    # Disruption handling
    # ------------------------------------------------------------------
    def notify_disruption(self, segment: str, label: str = "") -> List[int]:
        """Called by the incident manager; flags missions standing on it."""
        affected = []
        with self._lock:
            missions = list(self._missions.values())
        for mission in missions:
            if mission.status in ("completed", "aborted"):
                continue
            with mission.route_lock:
                leg = mission.current_leg
                if not leg or mission.status.endswith("_paused"):
                    continue
                if any(segment_key(str(u), str(v)) == segment for u, v, _k in leg.result.edge_path):
                    mission.pending_reroute_reason = label or "Route disrupted"
                    affected.append(mission.id)
        return affected

    # ------------------------------------------------------------------
    # Simulation loop
    # ------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mission-loop")
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            time.sleep(config.SIM_TICK_S)
            now = time.time()
            with self._lock:
                missions = list(self._missions.values())
            for mission in missions:
                try:
                    self._tick(mission, now)
                except Exception as exc:  # never let one mission kill the loop
                    mission.log("error", f"Simulation error: {exc}")

    def _tick(self, mission: Mission, now: float) -> None:
        if mission.status in ("completed", "aborted") or mission.status.endswith("_paused"):
            return
        with mission.route_lock:
            dt = max(0.0, now - mission.last_tick)
            mission.last_tick = now

            if mission.pending_reroute_reason:
                reason = mission.pending_reroute_reason
                mission.pending_reroute_reason = None
                self._reroute(mission, reason)
                return

            if mission.status == "on_scene":
                if now >= mission.dwell_until:
                    mission.status = "en_route_hospital"
                    mission.leg_index = 1
                    mission.distance_travelled_m += 0
                    mission.log("pickup", "Patient collected. En route to hospital.")
                return

            leg = mission.current_leg
            if leg is None:
                return

            # Check if this mission is driven by live hardware GPS telemetry
            is_live_active = (
                mission.is_live_telemetry
                and mission.last_telemetry_time is not None
                and (now - mission.last_telemetry_time) <= config.LIVE_GPS_STALE_TIMEOUT_S
            )

            if not is_live_active:
                if mission.is_live_telemetry and (now - (mission.last_telemetry_time or 0)) > config.LIVE_GPS_STALE_TIMEOUT_S:
                    mission.is_live_telemetry = False
                    mission.telemetry_source = "simulated"
                    mission.log("telemetry", "Live GPS signal stale (>30s); falling back to simulated kinematics.")

                advance = _arc_speed(leg) * dt
                while advance > 0 and not leg.at_end:
                    idx = leg.edge_index
                    free = max(0.0, leg.edge_length(idx) - leg.edge_progress_m)
                    step = min(advance, free)
                    leg.edge_progress_m += step
                    leg.distance_covered_m += step
                    mission.distance_travelled_m += step
                    advance -= step
                    if leg.edge_progress_m >= leg.edge_length(idx) - 1e-6:
                        leg.edge_index += 1
                        leg.edge_progress_m = 0.0

                pos = self.engine.position_along(leg.result.coordinates, leg.distance_covered_m)
                if pos:
                    (lat, lng), seg_idx = pos
                    mission.heading = _heading(leg.result.coordinates, seg_idx)
                    mission.position = [lat, lng]

            # Is the road we are on (or about to enter) closed or heavily jammed?
            blocked = self._blocking_edge(leg)
            if blocked:
                mission.log("disruption", "Road closure or blockage detected ahead of ambulance.")
                self._reroute(mission, "Street ahead closed")
                return

            if leg.at_end or leg.remaining_m <= 1.0:
                if mission.leg_index == 0:
                    mission.status = "on_scene"
                    mission.dwell_until = now + PICKUP_DWELL_S / max(config.SIM_SPEED_FACTOR, 0.1)
                    mission.log("arrived", "Ambulance arrived at the emergency site.")
                else:
                    mission.status = "completed"
                    mission.finished_at = now
                    mission.position = [mission.hospital["lat"], mission.hospital["lng"]]
                    mission.log("completed", f"Patient delivered to {mission.hospital['name']}.")
                    self._archive(mission)

    def _blocking_edge(self, leg: Leg) -> bool:
        """True if the current or next two road segments are closed."""
        for u, v, _k in leg.result.edge_path[leg.edge_index:leg.edge_index + 3]:
            if self.traffic.is_blocked(u, v):
                return True
        return False

    def _reroute(self, mission: Mission, reason: str) -> None:
        leg = mission.current_leg
        if not leg or not mission.position:
            return
        cutting_node = self.engine.nearest_node(mission.position[0], mission.position[1])
        target = (mission.emergency["node"] if mission.leg_index == 0
                  else mission.hospital["node"])

        try:
            new_route = self.engine.route(cutting_node, target, mission.algorithm)
        except Exception:
            # fall back to the other algorithm before giving up
            other = "dijkstra" if mission.algorithm == "astar" else "astar"
            try:
                new_route = self.engine.route(cutting_node, target, other)
                mission.log("fallback",
                            f"{mission.algorithm.upper()} found no route; used {other.upper()}.")
            except Exception as exc:
                mission.log("stranded", f"No alternative route available: {exc}")
                mission.status += "_paused"
                return

        new_leg = self._make_leg(leg.name, new_route)
        before_eta = leg.remaining_m / max(_arc_speed(leg), 1e-6)
        after_eta = new_leg.result.total_time_s
        mission.legs[mission.leg_index] = new_leg
        mission.reroutes += 1
        mission.log(
            "reroute",
            f"REROUTED ({reason}). {mission.algorithm.upper()} expanded "
            f"{new_route.nodes_expanded} nodes in {new_route.compute_ms:.2f} ms.",
            reason=reason,
            old_eta_s=round(before_eta, 1),
            new_eta_s=round(after_eta, 1),
            nodes_expanded=new_route.nodes_expanded,
            compute_ms=round(new_route.compute_ms, 3),
        )

    def _archive(self, mission: Mission) -> None:
        report = self.report(mission)
        self.mission_log.append(report)
        return None

    # ------------------------------------------------------------------
    # Reports / analytics
    # ------------------------------------------------------------------
    def report(self, mission: Mission) -> Dict[str, Any]:
        original = mission.original_route or {}
        original_total = original.get("estimated_total_s", 0.0)
        current_total = sum(l.result.total_time_s for l in mission.legs)
        actual = None
        if mission.started_at:
            actual = round((mission.finished_at or time.time()) - mission.started_at, 1)
        return {
            "mission_id": mission.id,
            "status": mission.status,
            "algorithm": mission.algorithm,
            "ambulance": mission.ambulance_label,
            "emergency": mission.emergency,
            "hospital": {"name": mission.hospital.get("name"), "lat": mission.hospital.get("lat"),
                         "lng": mission.hospital.get("lng")},
            "reroutes": mission.reroutes,
            "distance_travelled_m": round(mission.distance_travelled_m, 1),
            "original_estimated_s": round(original_total, 1),
            "current_estimated_s": round(current_total, 1),
            "time_difference_s": round(current_total - original_total, 1),
            "simulation_elapsed_s": actual,
            "segments": sum(len(l.result.edge_path) for l in mission.legs),
            "nodes_expanded": sum(l.result.nodes_expanded for l in mission.legs),
            "events": mission.events,
        }
