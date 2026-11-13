"""End-to-end demo test against the running Flask app.

Reproduces the signature demonstration:
  plan mission -> ambulance moves -> close a road ahead -> automatic reroute.

    python scripts/api_demo_test.py
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:5000"


def call(path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode()[:300]}


def main():
    print("1. reset + ambulance")
    call("/api/traffic/reset", {})
    amb = call("/api/ambulances", {"lat": 28.6400, "lng": 77.2100})["ambulance"]
    print("   ", amb["id"], amb["label"])

    print("2. hospitals near India Gate")
    hospitals = call("/api/hospitals?lat=28.6129&lng=77.2295&limit=6")["items"]
    for h in hospitals[:6]:
        print(f"    {h['distance_m']/1000:5.2f} km  {h['name']}  [{h['type']}]")
    hospital = next((h for h in hospitals if h["type"] == "hospital"), hospitals[0])

    print("3. dispatch mission")
    res = call("/api/missions", {
        "ambulance_id": amb["id"], "hospital_id": hospital["id"],
        "emergency_lat": 28.6129, "emergency_lng": 77.2295,
        "algorithm": "astar", "auto_start": True,
    })
    if not res.get("ok"):
        print("   FAILED:", res)
        return 1
    mission = res["mission"]
    mid = mission["id"]
    print(f"    mission #{mid}  leg={mission['leg_name']}  eta={mission['eta_s']}s "
          f"coords={len(mission['current_leg_coordinates'])}")
    cmp = res.get("comparison", {})
    if cmp:
        print(f"    A* {cmp['astar']['avg_compute_ms']}ms/{cmp['astar']['avg_nodes_expanded']} nodes  vs "
              f"Dijkstra {cmp['dijkstra']['avg_compute_ms']}ms/{cmp['dijkstra']['avg_nodes_expanded']} nodes "
              f"-> {cmp['speedup_x']}x")

    print("4. letting the ambulance drive...")
    time.sleep(4)
    live = call(f"/api/missions/{mid}")["mission"]
    print(f"    status={live['status']} travelled={live['distance_travelled_m']}m "
          f"pos={live['position']}")

    print("5. closing a road ahead of the ambulance")
    steps = live["legs"][live["leg_index"]]["steps"]
    travelled = live["distance_travelled_m"]
    acc, target = 0.0, None
    for st in steps:
        acc += st["distance_m"]
        if acc > travelled + 120:      # a little ahead of the vehicle
            target = st
            break
    if not target:
        print("    (route too short to interrupt; using last step)")
        target = steps[-1]
    print(f"    blocking '{target['street']}' ({target['distance_m']}m ahead)")
    inc = call("/api/incidents", {
        "u": target["from"][0], "v": target["to"][0], "kind": "closure",
        "level": "closed", "label": "Demo closure",
        "lat": target["from"][1], "lng": target["from"][2],
    })
    print("    incident:", inc.get("incident", {}).get("id"), "affected missions:",
          inc.get("missions_affected"))

    print("6. waiting for automatic reroute...")
    for _ in range(12):
        time.sleep(1)
        m = call(f"/api/missions/{mid}")["mission"]
        if m["reroutes"]:
            ev = [e for e in m["events"] if e["kind"] == "reroute"][-1]
            print(f"    REROUTED after {ev['t']}s: {ev['message']}")
            print(f"    old ETA {ev.get('old_eta_s')}s -> new ETA {ev.get('new_eta_s')}s")
            break
    else:
        print("    WARNING: no reroute observed")
        return 1

    print("7. mission report")
    rep = call(f"/api/missions/{mid}/report")["report"]
    print(json.dumps({k: rep[k] for k in (
        "mission_id", "status", "reroutes", "distance_travelled_m",
        "original_estimated_s", "current_estimated_s", "time_difference_s", "segments")}, indent=2))
    print("\nDEMO TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
