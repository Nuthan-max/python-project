"""
Hospitals and emergency facilities.

Locations are REAL OpenStreetMap features (`amenity=hospital|clinic|doctors`)
near the covered area. Names/positions are OSM-derived and **unverified**
until checked against institutional sources — the API exposes a
`verified` flag that stays False for every record.

Bed availability / capacity in the UI is SIMULATED.

Candidates are cached to data/cache/delhi_nct_hospital_candidates.json so
the app can run without network access after the first build. The previous
Central Delhi cache is retained and merged into the expanded dataset.
"""

from __future__ import annotations

import json
import math
import random
import time
from typing import Any, Dict, List

import config

try:
    import osmnx as ox
except Exception:  # pragma: no cover
    ox = None


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class FacilityManager:
    def __init__(self) -> None:
        self._facilities: List[Dict[str, Any]] = []
        self._availability: Dict[str, Dict[str, Any]] = {}
        self._last_refresh = 0.0

    # ------------------------------------------------------------------
    def load(self, force: bool = False) -> List[Dict[str, Any]]:
        if config.FACILITIES_PATH.exists() and not force:
            data = json.loads(config.FACILITIES_PATH.read_text(encoding="utf-8"))
            self._facilities = data.get("facilities", [])
            self._last_refresh = data.get("fetched_at", 0.0)
        elif ox is not None:
            existing = self._load_legacy()
            try:
                downloaded = self._download()
            except Exception as exc:
                if not existing:
                    raise
                print(f"[facilities] Delhi-wide refresh failed; retaining "
                      f"{len(existing)} cached Central Delhi facilities: {exc}")
                self._facilities = existing
            else:
                self._facilities = self._merge_facilities(existing, downloaded)
                self._last_refresh = time.time()
                config.FACILITIES_PATH.write_text(json.dumps({
                    "fetched_at": self._last_refresh,
                    "area": config.AREA_NAME,
                    "source": "OpenStreetMap (amenity=hospital/clinic/doctors)",
                    "retained_existing_count": len(existing),
                    "facilities": self._facilities,
                }, indent=2), encoding="utf-8")
        elif config.LEGACY_FACILITIES_PATH.exists():
            self._facilities = self._load_legacy()
        else:
            self._facilities = []
        self._ensure_availability()
        return self._facilities

    def _load_legacy(self) -> List[Dict[str, Any]]:
        if not config.LEGACY_FACILITIES_PATH.exists():
            return []
        data = json.loads(config.LEGACY_FACILITIES_PATH.read_text(encoding="utf-8"))
        self._last_refresh = data.get("fetched_at", 0.0)
        return data.get("facilities", [])

    @staticmethod
    def _merge_facilities(existing: List[Dict[str, Any]],
                          downloaded: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for facility in [*existing, *downloaded]:
            key = (
                facility["name"].strip().casefold(),
                round(float(facility["lat"]), 5),
                round(float(facility["lng"]), 5),
            )
            if key not in seen:
                seen.add(key)
                merged.append(facility)
        merged.sort(key=lambda row: (0 if row["type"] == "hospital" else 1, row["name"]))
        return merged

    def _download(self) -> List[Dict[str, Any]]:
        print(f"[facilities] querying OpenStreetMap facilities across {config.AREA_NAME}...")
        rows: List[Dict[str, Any]] = []
        errors = []
        for tag_value in config.HOSPITAL_TAGS["amenity"]:
            try:
                gdf = ox.features_from_place(
                    config.PLACE_QUERIES[0], tags={"amenity": tag_value},
                )
            except Exception as exc:
                print(f"[facilities] {tag_value} query failed: {exc}")
                errors.append(exc)
                continue
            gdf = gdf[gdf.geometry.notna()]
            for idx, row in gdf.iterrows():
                geom = row.geometry
                try:
                    point = geom.representative_point() if hasattr(geom, "representative_point") else geom.centroid
                except Exception:
                    continue
                name = row.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                rows.append({
                    "id": f"osm-{tag_value}-{idx}",
                    "name": name.strip(),
                    "type": tag_value,
                    "lat": round(float(point.y), 6),
                    "lng": round(float(point.x), 6),
                    "address": _first_str(row.get("addr:street")),
                    "phone": _first_str(row.get("phone") or row.get("contact:phone")),
                    "emergency": _first_str(row.get("emergency")) or "unknown",
                    "operator": _first_str(row.get("operator")),
                    "verified": False,
                    "source": "OpenStreetMap",
                })
        if not rows and errors:
            raise RuntimeError(
                f"OpenStreetMap facility queries failed for {config.AREA_NAME}: {errors[-1]}"
            )
        unique = self._merge_facilities([], rows)
        print(f"[facilities] found {len(unique)} named facilities across {config.AREA_NAME}")
        return unique

    # ------------------------------------------------------------------
    def _ensure_availability(self) -> None:
        """Simulated, clearly-labelled availability for each facility."""
        rng = random.Random(42)
        for f in self._facilities:
            if f["id"] not in self._availability:
                self._availability[f["id"]] = {
                    "beds_total": rng.choice([120, 180, 250, 350, 500, 750]),
                    "beds_available": rng.randint(3, 45),
                    "trauma_centre": rng.random() < 0.45,
                    "status": rng.choice(["available", "available", "busy", "full"]),
                    "simulated": True,
                    "beds_source": "SIMULATED (No public Delhi live hospital API exists)",
                    "geometry_source": "REAL (OpenStreetMap geometry)",
                }

    def update_live_availability(self, facility_id: str, beds_available: int, status: str = "available") -> Dict[str, Any]:
        """Allows injecting verified real hospital bed counts when an institution feed is available."""
        if facility_id not in self._availability:
            self._ensure_availability()
        self._availability[facility_id].update({
            "beds_available": int(beds_available),
            "status": status,
            "simulated": False,
            "beds_source": "LIVE OPERATOR FEED",
            "last_updated": time.strftime("%H:%M:%S")
        })
        return self.get(facility_id)

    def all(self, include_clinics: bool = True) -> List[Dict[str, Any]]:
        items = []
        for f in self._facilities:
            if not include_clinics and f["type"] != "hospital":
                continue
            item = dict(f)
            item.update(self._availability.get(f["id"], {}))
            items.append(item)
        return items

    def nearest(self, lat: float, lng: float, limit: int = 5,
                only_available: bool = False,
                include_clinics: bool = True) -> List[Dict[str, Any]]:
        items = self.all(include_clinics=include_clinics)
        for item in items:
            item["distance_m"] = round(_haversine_m(lat, lng, item["lat"], item["lng"]), 1)
        if only_available:
            items = [i for i in items if i.get("status") != "full"]
        items.sort(key=lambda i: i["distance_m"])
        return items[:limit]

    def get(self, facility_id: str) -> Dict[str, Any]:
        for item in self.all():
            if item["id"] == facility_id:
                return item
        raise KeyError(f"unknown facility: {facility_id}")


def _first_str(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


facility_manager = FacilityManager()
