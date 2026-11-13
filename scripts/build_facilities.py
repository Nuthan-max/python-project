"""Build data/cache/delhi_nct_hospital_candidates.json from OpenStreetMap.

Uses a direct Overpass API query (faster and more predictable than the
OSMnx feature wrapper for a single tag sweep). Queries the configured Delhi
NCT bounds, merges all existing cached facilities, and falls back to cached
data if the network is unavailable.

    python scripts/build_facilities.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = """
[out:json][timeout:180];
(
  node["amenity"~"^(hospital|clinic|doctors)$"]({south},{west},{north},{east});
  way["amenity"~"^(hospital|clinic|doctors)$"]({south},{west},{north},{east});
  relation["amenity"~"^(hospital|clinic|doctors)$"]({south},{west},{north},{east});
);
out center tags;
"""


def fetch() -> list[dict]:
    west, south, east, north = config.DELHI_BBOX
    query = QUERY.format(south=south, west=west, north=north, east=east)
    last_error = None
    for url in OVERPASS_ENDPOINTS:
        try:
            print(f"[facilities] querying {url} ...")
            resp = requests.post(url, data={"data": query}, timeout=180,
                                 headers={"User-Agent": "RescueRoute/1.0 (student project)"})
            resp.raise_for_status()
            return _parse(resp.json())
        except Exception as exc:  # try the next mirror
            last_error = exc
            print(f"[facilities] failed: {exc}")
    raise RuntimeError(f"all Overpass endpoints failed: {last_error}")


def _parse(payload: dict) -> list[dict]:
    out = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        if el["type"] == "node":
            lat, lng = el.get("lat"), el.get("lon")
        else:
            centre = el.get("center") or {}
            lat, lng = centre.get("lat"), centre.get("lon")
        if lat is None or lng is None:
            continue
        out.append({
            "id": f"osm-{el['type']}-{el['id']}",
            "name": name.strip(),
            "type": tags.get("amenity", "hospital"),
            "lat": round(float(lat), 6),
            "lng": round(float(lng), 6),
            "address": tags.get("addr:street", ""),
            "phone": tags.get("phone") or tags.get("contact:phone", ""),
            "emergency": tags.get("emergency", "unknown"),
            "operator": tags.get("operator", ""),
            "verified": False,
            "source": "OpenStreetMap",
        })
    seen, unique = set(), []
    for r in out:
        key = (r["name"].lower(), round(r["lat"], 5), round(r["lng"], 5))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (0 if r["type"] == "hospital" else 1, r["name"]))
    return unique


def main() -> int:
    try:
        facilities = fetch()
    except RuntimeError as exc:
        existing_path = next(
            (path for path in (config.FACILITIES_PATH, config.LEGACY_FACILITIES_PATH)
             if path.exists()),
            None,
        )
        if existing_path is None:
            raise
        cached = json.loads(existing_path.read_text(encoding="utf-8"))
        facilities = cached.get("facilities") if isinstance(cached, dict) else None
        if not isinstance(facilities, list) or any(
                not isinstance(facility, dict) for facility in facilities):
            raise ValueError(
                f"existing facilities cache is invalid: {config.FACILITIES_PATH}"
            ) from exc
        hospitals = sum(1 for facility in facilities
                        if facility.get("type") == "hospital")
        print(f"[facilities] refresh failed: {exc}")
        print(f"[facilities] keeping existing cache: {len(facilities)} facilities "
              f"({hospitals} hospitals) -> {existing_path}")
        return 0

    existing = []
    for path in (config.LEGACY_FACILITIES_PATH, config.FACILITIES_PATH):
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            records = cached.get("facilities", []) if isinstance(cached, dict) else []
            if isinstance(records, list) and all(isinstance(item, dict) for item in records):
                existing.extend(records)
    from core.facilities import FacilityManager
    retained_count = len(existing)
    facilities = FacilityManager._merge_facilities(existing, facilities)

    config.FACILITIES_PATH.write_text(json.dumps({
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "area": config.AREA_NAME,
        "source": "OpenStreetMap (amenity=hospital|clinic) via Overpass API",
        "verified": False,
        "retained_existing_count": retained_count,
        "facilities": facilities,
    }, indent=2), encoding="utf-8")
    print(f"[facilities] saved {len(facilities)} facilities "
          f"({sum(1 for f in facilities if f['type'] == 'hospital')} hospitals) "
          f"-> {config.FACILITIES_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
