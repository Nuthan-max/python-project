"""SQLite persistence for incidents, missions and mission reports."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict, List

import config

_local = threading.local()
_init_lock = threading.Lock()
_initialised = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    segment      TEXT NOT NULL,
    level        TEXT NOT NULL,
    label        TEXT,
    lat          REAL,
    lng          REAL,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS missions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ambulance      TEXT,
    algorithm      TEXT,
    emergency_lat  REAL,
    emergency_lng  REAL,
    hospital_name  TEXT,
    status         TEXT,
    reroutes       INTEGER DEFAULT 0,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at    TEXT
);

CREATE TABLE IF NOT EXISTS mission_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id      INTEGER,
    algorithm       TEXT,
    reroutes        INTEGER,
    distance_m      REAL,
    original_eta_s  REAL,
    final_eta_s     REAL,
    time_diff_s     REAL,
    payload         TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def get_conn() -> sqlite3.Connection:
    global _initialised
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    if not _initialised:
        with _init_lock:
            if not _initialised:
                conn.executescript(SCHEMA)
                conn.commit()
                _initialised = True
    return conn


def init_db() -> None:
    get_conn()


# ---------------------------------------------------------------------------
def log_incident(kind: str, segment: str, level: str, label: str,
                 lat: float, lng: float) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO incidents (kind, segment, level, label, lat, lng) VALUES (?,?,?,?,?,?)",
        (kind, segment, level, label, lat, lng),
    )
    conn.commit()
    return int(cur.lastrowid)


def record_mission(mission_id: int, ambulance: str, algorithm: str,
                   emergency: Dict[str, float], hospital: str, status: str) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO missions
           (id, ambulance, algorithm, emergency_lat, emergency_lng, hospital_name, status)
           VALUES (?,?,?,?,?,?,?)""",
        (mission_id, ambulance, algorithm, emergency.get("lat"), emergency.get("lng"),
         hospital, status),
    )
    conn.commit()


def update_mission(mission_id: int, status: str, reroutes: int) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE missions SET status=?, reroutes=?, "
        "finished_at=CASE WHEN ? IN ('completed','aborted') THEN CURRENT_TIMESTAMP ELSE finished_at END "
        "WHERE id=?",
        (status, reroutes, status, mission_id),
    )
    conn.commit()


def save_report(report: Dict[str, Any]) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO mission_reports
           (mission_id, algorithm, reroutes, distance_m, original_eta_s, final_eta_s, time_diff_s, payload)
           VALUES (?,?,?,?,?,?,?,?)""",
        (report.get("mission_id"), report.get("algorithm"), report.get("reroutes", 0),
         report.get("distance_travelled_m", 0), report.get("original_estimated_s", 0),
         report.get("current_estimated_s", 0), report.get("time_difference_s", 0),
         json.dumps(report, default=str)),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_reports(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mission_id, algorithm, reroutes, distance_m, original_eta_s, final_eta_s, "
        "time_diff_s, payload, created_at FROM mission_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_incidents(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def stats() -> Dict[str, Any]:
    conn = get_conn()
    missions = conn.execute("SELECT COUNT(*) AS c FROM missions").fetchone()["c"]
    reports = conn.execute("SELECT COUNT(*) AS c FROM mission_reports").fetchone()["c"]
    incidents = conn.execute("SELECT COUNT(*) AS c FROM incidents").fetchone()["c"]
    reroutes = conn.execute("SELECT COALESCE(SUM(reroutes),0) AS c FROM mission_reports").fetchone()["c"]
    avg_diff = conn.execute("SELECT COALESCE(AVG(time_diff_s),0) AS a FROM mission_reports").fetchone()["a"]
    return {
        "missions": missions,
        "reports": reports,
        "incidents": incidents,
        "total_reroutes": reroutes,
        "avg_time_difference_s": round(float(avg_diff), 1),
    }
