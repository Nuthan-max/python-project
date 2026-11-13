"""
RescueRoute — Flask backend.

Start the command centre:
    python app.py            (then open http://127.0.0.1:5000)

Endpoints (all JSON):
    GET  /api/status                  engine + data-source health
    GET  /api/network                 road network GeoJSON (cached)
    GET  /api/hospitals?lat=&lng=     facilities (real OSM, verified=false)
    POST /api/ambulances              place simulated ambulance
    GET  /api/ambulances
    POST /api/route                   compute a route (also returns A* vs Dijkstra)
    POST /api/incidents               create accident / closure / congestion
    POST /api/incidents/<id>/resolve  clear it
    GET  /api/incidents
    POST /api/traffic                 set congestion on a segment
    POST /api/traffic/reset
    POST /api/missions                plan + optionally start a mission
    POST /api/missions/<id>/start|pause|resume|abort
    GET  /api/missions/<id>           live mission state (poll ~1 s)
    GET  /api/missions/<id>/report    mission report
    GET  /api/reports                 finished mission reports
    GET  /api/incident-log            incident history from SQLite
"""

from __future__ import annotations

import itertools
import json
import math
import threading
import time
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request, send_file, Response

import config
from core import db
from core.facilities import facility_manager
from core.mission import MissionManager
from core.network import (download_delhi_graph, export_network_geojson,
                          load_graph, annotate_graph)
from core.routing import NoRouteError, RouteEngine
from core.traffic import segment_key, traffic_engine

app = Flask(__name__, static_folder=str(config.STATIC_DIR),
            template_folder=str(config.TEMPLATE_DIR))
app.config["JSON_SORT_KEYS"] = False

# ---------------------------------------------------------------------------
# Engine bootstrap (lazy, once)
# ---------------------------------------------------------------------------
_state: Dict[str, Any] = {"ready": False, "error": None}
_boot_lock = threading.Lock()

_ambulance_seq = itertools.count(1)
_ambulances: Dict[str, Dict[str, Any]] = {}


def _bootstrap() -> None:
    global _state
    if _state["ready"]:
        return
    with _boot_lock:
        if _state["ready"]:
            return
        try:
            network_area = config.AREA_NAME
            if config.GRAPH_PATH.exists():
                graph = annotate_graph(load_graph())
            else:
                try:
                    graph = download_delhi_graph(force=False)
                except Exception as exc:
                    if not config.LEGACY_GRAPH_PATH.exists():
                        raise
                    print(f"[app] Delhi-wide network download failed; retaining "
                          f"the existing Central Delhi graph: {exc}")
                    graph = annotate_graph(load_graph(config.LEGACY_GRAPH_PATH))
                    if config.LEGACY_GRAPH_META_PATH.exists():
                        legacy_meta = json.loads(
                            config.LEGACY_GRAPH_META_PATH.read_text(encoding="utf-8")
                        )
                        network_area = legacy_meta.get("area", "Central Delhi (cached fallback)")
            traffic_engine.register_graph_edges(
                (str(u), str(v), str(k)) for u, v, k in graph.edges(keys=True)
            )
            engine = RouteEngine(graph, traffic_engine)
            missions = MissionManager(engine, traffic_engine)
            facility_manager.load()
            db.init_db()
            traffic_engine.start_auto_traffic()
            traffic_engine.sync_live_traffic(engine)
            _state.update({
                "ready": True, "graph": graph, "engine": engine,
                "network_area": network_area,
                "missions": missions,
            })
            print(f"[app] ready — {graph.number_of_nodes()} nodes, "
                  f"{graph.number_of_edges()} edges, "
                  f"{len(facility_manager.all())} facilities")
        except Exception as exc:  # surfaced via /api/status
            _state["error"] = str(exc)
            print(f"[app] bootstrap failed: {exc}")


def ensure_ready():
    _bootstrap()
    if not _state["ready"]:
        raise RuntimeError(_state["error"] or "engine not ready")
    return _state


def engine() -> RouteEngine:
    return ensure_ready()["engine"]


def missions() -> MissionManager:
    return ensure_ready()["missions"]


def _error(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


def _json_body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template(
        "index.html",
        map_center=config.MAP_CENTER,
        map_zoom=config.MAP_ZOOM,
        tile_url=config.TILE_URL,
        attribution=config.TILE_ATTRIBUTION,
        has_maptiler_key=bool(config.MAPTILER_API_KEY),
        maptiler_style=config.MAPTILER_STYLE_ID,
    )


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/driver")
def driver_portal():
    """Lightweight mobile-responsive Driver GPS transmitter page."""
    return render_template("driver.html")


# ---------------------------------------------------------------------------
# Real-Time Telemetry & SSE Streaming ($0 Free Infrastructure)
# ---------------------------------------------------------------------------
@app.post("/api/telemetry/ambulance")
def api_ambulance_telemetry():
    """Receives live GPS updates from a smartphone or device."""
    ensure_ready()
    body = _json_body()
    amb_id = body.get("ambulance_id") or body.get("id")
    if not amb_id:
        return _error("ambulance_id is required")
    try:
        lat = float(body["lat"])
        lng = float(body["lng"])
    except (KeyError, TypeError, ValueError):
        return _error("lat and lng are required numbers")

    heading = float(body["heading"]) if "heading" in body and body["heading"] is not None else None
    speed = float(body["speed"]) if "speed" in body and body["speed"] is not None else None
    accuracy = float(body["accuracy"]) if "accuracy" in body and body["accuracy"] is not None else None

    unit = _ambulances.get(amb_id)
    if not unit:
        unit = {
            "id": amb_id,
            "label": body.get("label") or f"Ambulance {amb_id}",
            "lat": lat, "lng": lng,
            "status": "available",
            "created_at": time.time(),
        }
        _ambulances[amb_id] = unit
    else:
        unit["lat"] = lat
        unit["lng"] = lng
    unit["is_live"] = True
    unit["telemetry_source"] = "real_device_gps"
    unit["last_telemetry_time"] = time.time()
    unit["accuracy_m"] = accuracy

    mission = missions().update_live_telemetry(amb_id, lat, lng, heading=heading, speed_mps=speed, accuracy_m=accuracy)
    return jsonify({
        "ok": True,
        "ambulance": unit,
        "active_mission": mission.to_dict() if mission else None
    })


@app.get("/api/stream")
def api_stream():
    """Server-Sent Events endpoint pushing real-time updates to the frontend."""
    def event_stream():
        while True:
            time.sleep(1.2)
            try:
                active_missions = [m.to_dict() for m in missions().list_active()]
                traffic_status = traffic_engine.get_status()
                amb_list = list(_ambulances.values())
                payload = {
                    "missions": active_missions,
                    "traffic": traffic_status,
                    "ambulances": amb_list,
                    "timestamp": time.time()
                }
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception:
                pass
    return Response(event_stream(), mimetype="text/event-stream")


@app.get("/api/traffic/live-sync")
def api_traffic_live_sync():
    """Polls TomTom live traffic data (cached) or reports simulated traffic state."""
    ensure_ready()
    status = traffic_engine.sync_live_traffic(engine=engine())
    return jsonify({"ok": True, "status": status})


@app.post("/api/hospitals/<facility_id>/availability")
def api_update_hospital_availability(facility_id: str):
    """Allows manual or live verified bed updates."""
    ensure_ready()
    body = _json_body()
    try:
        beds = int(body.get("beds_available", 10))
        status = body.get("status", "available")
        updated = facility_manager.update_live_availability(facility_id, beds, status)
        return jsonify({"ok": True, "facility": updated})
    except Exception as exc:
        return _error(str(exc))


# ---------------------------------------------------------------------------
# Status / network
# ---------------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    ensure_ready()
    st = _state
    graph = st.get("graph")
    meta = {}
    if config.GRAPH_META_PATH.exists():
        import json
        meta = json.loads(config.GRAPH_META_PATH.read_text())
    return jsonify({
        "ok": True,
        "ready": st["ready"],
        "error": st.get("error"),
        "network": {
            "nodes": graph.number_of_nodes() if graph else 0,
            "edges": graph.number_of_edges() if graph else 0,
            "segments": traffic_engine.segment_count(),
            "area": st.get("network_area", config.AREA_NAME),
            "source": "OpenStreetMap via OSMnx",
            "downloaded_at": meta.get("downloaded_at"),
        },
        "facilities": {"count": len(facility_manager.all()), "verified": 0},
        "traffic": {
            "active_states": len(traffic_engine.snapshot()),
            "incidents": len(traffic_engine.incidents()),
        },
        "traffic_live": traffic_engine.get_status(),
        "live_ambulances": len([a for a in _ambulances.values() if a.get("is_live")]),
        "missions": len(missions().list_active()),
        "database": db.stats(),
        "disclaimer": _DISCLAIMER,
        "map": {"center": config.MAP_CENTER, "zoom": config.MAP_ZOOM},
    })


@app.get("/api/network")
def api_network():
    st = ensure_ready()
    bounds = tuple(request.args.get(key, type=float)
                   for key in ("west", "south", "east", "north"))
    if any(value is not None for value in bounds):
        if any(value is None for value in bounds):
            return _error("west, south, east and north are all required")
        west, south, east, north = bounds
        if west >= east or south >= north:
            return _error("invalid network bounds")
    else:
        bounds = None
    return jsonify(export_network_geojson(st["graph"], bbox=bounds))


@app.get("/api/hospitals")
def api_hospitals():
    ensure_ready()
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    limit = request.args.get("limit", default=12, type=int)
    only = request.args.get("available", "0") == "1"
    include_clinics = request.args.get("clinics", "1") == "1"
    if lat is not None and lng is not None:
        items = facility_manager.nearest(lat, lng, limit=limit, only_available=only,
                                         include_clinics=include_clinics)
    else:
        items = facility_manager.all(include_clinics=include_clinics)[:limit]
    return jsonify({
        "ok": True, "count": len(items), "items": items,
        "note": "Locations from OpenStreetMap, not independently verified. "
                "Bed availability is simulated.",
    })


# ---------------------------------------------------------------------------
# Ambulances
# ---------------------------------------------------------------------------
@app.post("/api/ambulances")
def api_add_ambulance():
    ensure_ready()
    body = _json_body()
    try:
        lat = float(body["lat"])
        lng = float(body["lng"])
    except (KeyError, TypeError, ValueError):
        return _error("lat and lng are required")
    n = next(_ambulance_seq)
    unit = {
        "id": f"AMB-{n:03d}",
        "label": body.get("label") or f"Ambulance {n:03d}",
        "lat": lat, "lng": lng,
        "status": "available",
        "created_at": time.time(),
    }
    _ambulances[unit["id"]] = unit
    return jsonify({"ok": True, "ambulance": unit})


@app.get("/api/ambulances")
def api_list_ambulances():
    return jsonify({"ok": True, "items": list(_ambulances.values())})


@app.delete("/api/ambulances/<ambulance_id>")
def api_delete_ambulance(ambulance_id: str):
    _ambulances.pop(ambulance_id, None)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
@app.post("/api/route")
def api_route():
    eng = engine()
    body = _json_body()
    try:
        src = body.get("source_node") or eng.nearest_node(float(body["source_lat"]), float(body["source_lng"]))
        dst = body.get("target_node") or eng.nearest_node(float(body["target_lat"]), float(body["target_lng"]))
        algo = body.get("algorithm") or config.DEFAULT_ALGORITHM
        result = eng.route(str(src), str(dst), algo)
    except (KeyError, ValueError, TypeError) as exc:
        return _error(f"missing/invalid input: {exc}")
    except NoRouteError as exc:
        return _error(str(exc), 409)
    payload = {"ok": True, "route": result.to_dict()}
    if body.get("compare"):
        comp = eng.compare(str(src), str(dst), runs=config.BENCHMARK_RUNS)
        alt = comp.get("dijkstra_route") if algo == "astar" else comp.get("astar_route")
        payload["alternative_route"] = alt
        payload["comparison"] = comp
        payload["comparison"].pop("astar_route", None)
        payload["comparison"].pop("dijkstra_route", None)
    return jsonify(payload)


# ---------------------------------------------------------------------------
# Incidents & traffic
# ---------------------------------------------------------------------------
LEVELS = set(config.CONGESTION_MULTIPLIERS) | {"closed"}


def _segment_from_request(body: Dict[str, Any]) -> str:
    if body.get("segment"):
        return str(body["segment"])
    u, v = body.get("u"), body.get("v")
    if u is None or v is None:
        raise ValueError("segment or u/v required")
    return segment_key(str(u), str(v))


@app.post("/api/incidents")
def api_create_incident():
    ensure_ready()
    body = _json_body()
    kind = (body.get("kind") or "closure").lower()
    default_level = {"accident": "jam", "closure": "closed", "traffic": "heavy"}.get(kind, "closed")
    level = body.get("level") or default_level
    if level not in LEVELS:
        return _error(f"level must be one of {sorted(LEVELS)}")
    try:
        seg = _segment_from_request(body)
        lat = float(body.get("lat", config.MAP_CENTER[0]))
        lng = float(body.get("lng", config.MAP_CENTER[1]))
        label = body.get("label") or {
            "accident": "Simulated accident", "closure": "Simulated road closure",
            "traffic": "Simulated congestion",
        }.get(kind, "Simulated incident")
        incident = traffic_engine.add_incident(seg, kind, level, label, lat, lng)
    except (KeyError, ValueError) as exc:
        return _error(str(exc))

    db.log_incident(kind, seg, level, label, lat, lng)
    affected = missions().notify_disruption(seg, label)
    return jsonify({
        "ok": True,
        "incident": incident.to_dict(),
        "missions_affected": affected,
        "will_reroute": bool(affected),
    })


@app.post("/api/incidents/<int:incident_id>/resolve")
def api_resolve_incident(incident_id: int):
    ensure_ready()
    ok = traffic_engine.resolve_incident(incident_id)
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.get("/api/incidents")
def api_list_incidents():
    ensure_ready()
    try:
        traffic_engine.sync_live_traffic(engine=engine())
    except Exception:
        pass
    return jsonify({"ok": True, "items": traffic_engine.incidents()})


@app.get("/api/incident-log")
def api_incident_log():
    ensure_ready()
    return jsonify({"ok": True, "items": db.list_incidents()})


@app.post("/api/traffic")
def api_set_traffic():
    ensure_ready()
    body = _json_body()
    level = body.get("level", "clear")
    if level not in LEVELS:
        return _error(f"level must be one of {sorted(LEVELS)}")
    try:
        seg = _segment_from_request(body)
        traffic_engine.set_traffic(seg, level, body.get("reason", "Simulated traffic"))
    except (KeyError, ValueError) as exc:
        return _error(str(exc))
    return jsonify({"ok": True, "segment": seg, "level": level})


@app.post("/api/traffic/reset")
def api_reset_traffic():
    ensure_ready()
    traffic_engine.clear_all()
    return jsonify({"ok": True, "message": "All simulated conditions cleared."})


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------
@app.post("/api/missions")
def api_create_mission():
    mgr = missions()
    body = _json_body()
    try:
        amb_id = str(body["ambulance_id"])
        unit = _ambulances[amb_id]
        facility = facility_manager.get(str(body["hospital_id"]))
        mission = mgr.create_mission(
            ambulance_id=amb_id,
            ambulance_label=unit["label"],
            ambulance_lat=unit["lat"],
            ambulance_lng=unit["lng"],
            emergency_lat=float(body["emergency_lat"]),
            emergency_lng=float(body["emergency_lng"]),
            hospital=facility,
            algorithm=body.get("algorithm") or config.DEFAULT_ALGORITHM,
        )
    except KeyError as exc:
        return _error(f"unknown id: {exc}")
    except (ValueError, TypeError) as exc:
        return _error(str(exc))
    except NoRouteError as exc:
        return _error(str(exc), 409)
    except RuntimeError as exc:   # e.g. facility cache missing
        return _error(str(exc), 503)

    unit["status"] = "on_mission"
    db.record_mission(mission.id, unit["label"], mission.algorithm,
                      mission.emergency, facility["name"], mission.status)

    if body.get("auto_start", True):
        mgr.start(mission.id)

    return jsonify({
        "ok": True,
        "mission": mission.to_dict(engine()),
        "comparison": _safe_compare(mgr, mission),
    })


def _safe_compare(mgr: MissionManager, mission) -> Dict[str, Any]:
    """Benchmark both algorithms on the mission's planned first leg.

    Comparing a very short trip is meaningless (handful of nodes), so the
    benchmark uses ambulance -> emergency -> hospital in one combined pair,
    which is the trip the dispatcher actually cares about.
    """
    try:
        eng = mgr.engine
        start = mission.legs[0].result.node_path[0]
        target = mission.hospital["node"]
        return eng.compare(start, target, runs=config.BENCHMARK_RUNS)
    except Exception:
        return {}


@app.get("/api/missions/<int:mission_id>")
def api_mission(mission_id: int):
    mgr = missions()
    try:
        mission = mgr.get(mission_id)
    except KeyError as exc:
        return _error(str(exc), 404)
    return jsonify({"ok": True, "mission": mission.to_dict(mgr.engine)})


@app.get("/api/missions/<int:mission_id>/report")
def api_mission_report(mission_id: int):
    mgr = missions()
    try:
        mission = mgr.get(mission_id)
    except KeyError as exc:
        return _error(str(exc), 404)
    return jsonify({"ok": True, "report": mgr.report(mission)})


@app.post("/api/missions/<int:mission_id>/<action>")
def api_mission_action(mission_id: int, action: str):
    mgr = missions()
    handlers = {"start": mgr.start, "pause": mgr.pause,
                "resume": mgr.resume, "abort": mgr.abort}
    if action not in handlers:
        return _error("unknown action", 404)
    try:
        mission = handlers[action](mission_id)
    except KeyError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc), 409)

    db.update_mission(mission.id, mission.status, mission.reroutes)
    if action == "abort":
        db.save_report(mgr.report(mission))
        for unit in _ambulances.values():
            if unit["id"] == mission.ambulance_id:
                unit["status"] = "available"
    return jsonify({"ok": True, "mission": mission.to_dict(mgr.engine)})


@app.get("/api/missions")
def api_list_missions():
    mgr = missions()
    return jsonify({"ok": True, "items": [m.to_dict(mgr.engine) for m in mgr.list_active()]})


@app.get("/api/reports")
def api_reports():
    ensure_ready()
    live = missions().list_reports()
    return jsonify({"ok": True, "items": live, "db": db.list_reports(limit=30)})


@app.errorhandler(Exception)
def handle_unexpected(exc):
    if isinstance(exc, RuntimeError):
        return _error(str(exc), 503)
    app.logger.exception(exc)
    return _error(f"internal error: {exc}", 500)


_DISCLAIMER = {
    "real": ["Delhi road network geometry (OpenStreetMap / OSMnx)",
             "Hospital & clinic locations (OpenStreetMap, unverified)"],
    "simulated": ["Traffic congestion", "Accidents", "Road closures",
                  "Ambulance positions", "Hospital bed availability", "Travel times"],
    "not_integrated": ["Live GPS", "Government traffic feeds", "Live accident detection",
                       "Real emergency dispatch"],
}


if __name__ == "__main__":
    print("[app] bootstrapping RescueRoute engine...")
    _bootstrap()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
