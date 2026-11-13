"""
RescueRoute — central configuration.

Every tunable assumption (speed estimates, area coverage, cache paths)
lives here so it is easy to audit and to cite in the project report.

IMPORTANT / DISCLAIMER
----------------------
Road geometry comes from OpenStreetMap (real data).
Travel-time speeds below are DOCUMENTED ESTIMATES derived from road
classification, NOT measured Delhi traffic. Traffic congestion, accidents,
road closures and ambulance/vehicle positions are SIMULATED.
Nothing produced by this application is a real emergency dispatch.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"
DB_PATH = DATA_DIR / "rescueroute.db"

GRAPH_PATH = CACHE_DIR / "delhi_nct_drive.graphml"
GRAPH_META_PATH = CACHE_DIR / "delhi_nct_graph_meta.json"
LEGACY_GRAPH_PATH = CACHE_DIR / "delhi_drive.graphml"
LEGACY_GRAPH_META_PATH = CACHE_DIR / "graph_meta.json"
FACILITIES_PATH = CACHE_DIR / "delhi_nct_hospital_candidates.json"
LEGACY_FACILITIES_PATH = CACHE_DIR / "hospital_candidates.json"

DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Area of interest: full National Capital Territory of Delhi (OSM geocoding).
# ---------------------------------------------------------------------------
AREA_NAME = "Delhi National Capital Territory"
PLACE_QUERIES = ["Delhi, India"]
NETWORK_TYPE = "drive"          # drivable road network
OSMNX_SETTINGS = {
    "use_cache": True,
    "log_console": False,
    "requests_timeout": 180,
}

# Map view defaults (Leaflet) — full Delhi region
MAP_CENTER = (28.6139, 77.2090)
MAP_ZOOM = 10

# ---------------------------------------------------------------------------
# Speed model (km/h) — ESTIMATES by OSM highway class, not measured traffic.
# Source of classes: OpenStreetMap highway tag. Values are conservative
# urban-Delhi assumptions documented in README.
# ---------------------------------------------------------------------------
DEFAULT_SPEED_KPH = 25.0
HIGHWAY_SPEEDS_KPH = {
    "motorway": 70.0,
    "motorway_link": 50.0,
    "trunk": 60.0,
    "trunk_link": 40.0,
    "primary": 45.0,
    "primary_link": 35.0,
    "secondary": 40.0,
    "secondary_link": 30.0,
    "tertiary": 30.0,
    "tertiary_link": 25.0,
    "unclassified": 25.0,
    "residential": 20.0,
    "living_street": 15.0,
    "service": 15.0,
    "road": 25.0,
}

# Ambulance urgency multiplier applied to free-flow speeds
EMERGENCY_SPEED_FACTOR = 1.15

# ---------------------------------------------------------------------------
# Traffic simulation model
# ---------------------------------------------------------------------------
CONGESTION_MULTIPLIERS = {
    "clear": 1.00,
    "moderate": 1.50,
    "heavy": 2.50,
    "jam": 4.00,
}
DEFAULT_CONGESTION = "clear"

# Simulated traffic somehow occasionally changes on its own (demo realism)
AUTO_TRAFFIC_EVENTS = True
AUTO_TRAFFIC_INTERVAL_S = 12.0

# Simulation tick (seconds of real time per simulation step)
SIM_TICK_S = 1.0
# How many metres the ambulance covers per real second (display speed-up)
SIM_SPEED_FACTOR = 6.0

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
DEFAULT_ALGORITHM = "astar"     # 'dijkstra' | 'astar'
BENCHMARK_RUNS = 5              # runs per algorithm when comparing

# ---------------------------------------------------------------------------
# Hospitals: OSM tags queried across the Delhi NCT boundary. Names and
# locations must still be verified before use in any demonstration.
# ---------------------------------------------------------------------------
HOSPITAL_TAGS = {"amenity": ["hospital", "clinic", "doctors"]}

# ---------------------------------------------------------------------------
# Tile server for Leaflet — MapTiler Cloud (raster tiles)
# Format: https://api.maptiler.com/maps/streets-v4/256/{z}/{x}/{y}.png?key=YOUR_API_KEY
# ---------------------------------------------------------------------------
MAPTILER_API_KEY = os.getenv("MAPTILER_API_KEY", "").strip()
MAPTILER_STYLE_ID = os.getenv("MAPTILER_STYLE_ID", "streets-v4").strip() or "streets-v4"

if MAPTILER_API_KEY:
    TILE_URL = (
        f"https://api.maptiler.com/maps/{MAPTILER_STYLE_ID}/256/"
        f"{{z}}/{{x}}/{{y}}.png?key={MAPTILER_API_KEY}"
    )
    TILE_ATTRIBUTION = (
        '<a href="https://www.maptiler.com/copyright/" target="_blank">&copy; MapTiler</a> '
        '<a href="https://www.openstreetmap.org/copyright" target="_blank">&copy; OpenStreetMap contributors</a>'
    )
else:
    TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    TILE_ATTRIBUTION = (
        '<a href="https://www.openstreetmap.org/copyright" target="_blank">'
        '&copy; OpenStreetMap contributors</a>'
    )

# ---------------------------------------------------------------------------
# Real-Time Monitoring & Telemetry Settings ($0 Free Tier Options)
# ---------------------------------------------------------------------------
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY", "").strip()
# Delhi NCT bounding box: min_lon, min_lat, max_lon, max_lat.
DELHI_BBOX = (76.84, 28.40, 77.35, 28.89)
TOMTOM_SNAP_MAX_DISTANCE_M = 1500
LIVE_TRAFFIC_CACHE_TTL_S = 180  # Cache live traffic 3 mins to strictly preserve 0-cost free quota
LIVE_GPS_STALE_TIMEOUT_S = 30   # Mark live ambulance offline if no ping in 30s
