"""Prepare persistent runtime data, then serve the Flask app with Gunicorn."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from core.network import download_delhi_graph


def main() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not config.GRAPH_PATH.exists() and not config.LEGACY_GRAPH_PATH.exists():
        print(
            "[startup] road graph is missing; downloading it to the persistent "
            "data volume. The first start can take several minutes.",
            flush=True,
        )
        download_delhi_graph()

    port = os.environ.get("PORT", "8000")
    command = [
        sys.executable,
        "-m",
        "gunicorn",
        "--bind",
        f"0.0.0.0:{port}",
        "--workers",
        "1",
        "--threads",
        "8",
        "--timeout",
        "300",
        "app:app",
    ]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
