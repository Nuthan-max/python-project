"""Headless smoke test: routing, Dijkstra vs A*, dynamic rerouting.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from core.network import annotate_graph, load_graph
from core.routing import NoRouteError, RouteEngine
from core.traffic import segment_key, traffic_engine


def main() -> int:
    print("Loading real Delhi graph...")
    G = annotate_graph(load_graph())
    traffic_engine.register_graph_edges((str(u), str(v), str(k)) for u, v, k in G.edges(keys=True))
    eng = RouteEngine(G, traffic_engine)
    print(f"  nodes={G.number_of_nodes()} edges={G.number_of_edges()} "
          f"segments={traffic_engine.segment_count()}")

    # Default demo: India Gate -> AIIMS-ish point
    start = (28.6129, 77.2295)
    end = (28.5672, 77.2100)
    src = eng.nearest_node(*start)
    dst = eng.nearest_node(*end)
    print(f"snapped src={src} dst={dst}")

    print("\n-- Dijkstra vs A* ------------------------------------------------")
    cmp = eng.compare(src, dst, runs=3)
    base = {algo: cmp[algo]["total_time_s"] for algo in ("astar", "dijkstra")}
    base_path = cmp["astar_route"]["node_path"]
    for algo in ("astar", "dijkstra"):
        a = cmp[algo]
        print(f"  {algo:9s} time={a['total_time_s']:8.1f}s dist={a['total_distance_m']:8.1f}m "
              f"nodes={a['avg_nodes_expanded']:7.1f} compute={a['avg_compute_ms']:8.2f}ms")
    print(f"  same route: {cmp['same_optimal_route']}  speedup: {cmp['speedup_x']}x")

    print("\n-- Dynamic rerouting: close a street on the optimal route --------")
    # Pick a mid-route edge and close it
    edges = cmp["astar_route"]["steps"]
    mid = len(edges) // 2
    target_edge = None
    for step in edges[mid:] + edges[:mid]:
        u = step["from"][0]
        v = step["to"][0]
        seg = segment_key(u, v)
        if len(traffic_engine.edges_for_segment(seg)) > 1 or True:
            target_edge = (u, v, seg)
            break
    u, v, seg = target_edge
    incident = traffic_engine.add_incident(seg, "closure", "closed", "Smoke-test closure",
                                           (edges[mid]["from"][1] + edges[mid]["to"][1]) / 2,
                                           (edges[mid]["from"][2] + edges[mid]["to"][2]) / 2)
    print(f"  closed segment {seg} (incident #{incident.id})")
    t0 = time.perf_counter()
    alt = eng.route(src, dst, "astar")
    print(f"  rerouted: time={alt.total_time_s:.1f}s dist={alt.total_distance_m:.1f}m "
          f"nodes={alt.nodes_expanded} in {alt.compute_ms:.2f}ms")
    assert alt.node_path != base_path, "reroute produced the same path"
    assert not any(segment_key(a, b) == seg for a, b, _ in alt.edge_path), "closure still used"
    print("  PASS: alternative route avoids the closure")

    # Dijkstra must still be feasible under closure
    d_alt = eng.route(src, dst, "dijkstra")
    print(f"  dijkstra under closure: {d_alt.total_time_s:.1f}s")

    traffic_engine.resolve_incident(incident.id)
    restored = eng.route(src, dst, "astar")
    print(f"  after clearing incident: {restored.total_time_s:.1f}s")
    assert abs(restored.total_time_s - base["astar"]) < 0.1, (restored.total_time_s, base)
    print("  PASS: conditions restored")

    # Unreachable-target behaviour
    print("\n-- Disconnected destination handling -----------------------------")
    fake = max(G.nodes(), key=lambda n: str(n))
    traffic_engine.clear_all()
    try:
        eng.route(src, fake, "astar")
        print("  (route exists to arbitrary node)")
    except NoRouteError as exc:
        print(f"  handled: {exc}")

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
