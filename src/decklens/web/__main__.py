"""Run the DeckLens web app locally.

    python -m decklens.web                  # http://127.0.0.1:8000
    python -m decklens.web --port 8080

Binds to 127.0.0.1 (this computer only). Exposing it to a network belongs
behind HTTPS and a reverse proxy, which comes with deployment.
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from ..config import ConfigError
from .app import create_app_from_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DeckLens web app.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        app = create_app_from_env()
    except ConfigError as exc:
        logging.getLogger("decklens.web").error("%s", exc)
        sys.exit(2)
    uvicorn.run(app, host="127.0.0.1", port=args.port, server_header=False, log_level="info")


if __name__ == "__main__":
    main()
