import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from core.facilities import FacilityManager
from core.traffic import TrafficEngine


class FacilityApiTests(unittest.TestCase):
    def test_bundled_cache_returns_nearby_hospitals_without_engine(self):
        client = app_module.app.test_client()

        with patch.object(app_module, "ensure_ready", side_effect=AssertionError):
            response = client.get(
                "/api/hospitals?lat=28.937256&lng=77.23526&limit=5&clinics=0"
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertGreater(payload["count"], 0)
        self.assertTrue(all(item["type"] == "hospital" for item in payload["items"]))

    def test_hospital_endpoint_does_not_require_routing_engine(self):
        facility = {
            "id": "osm-hospital-1",
            "name": "Example Hospital",
            "type": "hospital",
            "lat": 28.6,
            "lng": 77.2,
            "distance_m": 1200.0,
            "verified": False,
        }
        client = app_module.app.test_client()

        with (
            patch.object(app_module, "ensure_ready", side_effect=AssertionError),
            patch.object(app_module.facility_manager, "load"),
            patch.object(app_module.facility_manager, "nearest", return_value=[facility]),
        ):
            response = client.get(
                "/api/hospitals?lat=28.6&lng=77.2&clinics=0"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"][0]["id"], facility["id"])

    def test_legacy_hospital_cache_is_used_before_network_refresh(self):
        facility = {
            "id": "osm-hospital-1",
            "name": "Example Hospital",
            "type": "hospital",
            "lat": 28.6,
            "lng": 77.2,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / "legacy.json"
            legacy_path.write_text(
                json.dumps({"facilities": [facility]}), encoding="utf-8"
            )
            current_path = Path(temp_dir) / "current.json"
            manager = FacilityManager()

            with (
                patch("core.facilities.config.FACILITIES_PATH", current_path),
                patch("core.facilities.config.LEGACY_FACILITIES_PATH", legacy_path),
                patch("core.facilities.config.BUNDLED_FACILITIES_PATH", current_path.parent / "bundled.json"),
                patch("core.facilities.config.BUNDLED_LEGACY_FACILITIES_PATH", current_path.parent / "bundled-legacy.json"),
                patch.object(manager, "_download", side_effect=AssertionError),
            ):
                facilities = manager.load()

        self.assertEqual([item["id"] for item in facilities], [facility["id"]])

    def test_bundled_cache_is_used_when_volume_cache_is_empty(self):
        facility = {
            "id": "osm-hospital-2",
            "name": "Bundled Hospital",
            "type": "hospital",
            "lat": 28.6,
            "lng": 77.2,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            bundled_path = cache_dir / "bundled.json"
            bundled_path.write_text(
                json.dumps({"facilities": [facility]}), encoding="utf-8"
            )
            manager = FacilityManager()

            with (
                patch("core.facilities.config.FACILITIES_PATH", cache_dir / "volume.json"),
                patch("core.facilities.config.LEGACY_FACILITIES_PATH", cache_dir / "volume-legacy.json"),
                patch("core.facilities.config.BUNDLED_FACILITIES_PATH", bundled_path),
                patch("core.facilities.config.BUNDLED_LEGACY_FACILITIES_PATH", cache_dir / "bundled-legacy.json"),
                patch.object(manager, "_download", side_effect=AssertionError),
            ):
                facilities = manager.load()

        self.assertEqual([item["id"] for item in facilities], [facility["id"]])

    def test_tomtom_api_key_is_not_returned_in_provider_errors(self):
        secret = "test-tomtom-api-key"
        traffic = TrafficEngine()
        with (
            patch("core.traffic.config.TOMTOM_API_KEY", secret),
            patch(
                "core.traffic.requests.get",
                side_effect=TimeoutError(f"request to https://example.test/?key={secret} failed"),
            ),
        ):
            status = traffic.sync_live_traffic()

        self.assertIn("request failed", status["error"])
        self.assertNotIn(secret, str(status))


if __name__ == "__main__":
    unittest.main()
