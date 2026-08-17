#!/usr/bin/env python3
"""Cycling Progress Tracker — entry point.

Starts the local engine (FastAPI) on 127.0.0.1 and opens the UI in the default
browser. Everything stays on this machine.
"""

import argparse
import socket
import threading
import webbrowser

from cycling import config, storage

DEFAULT_PORT = 8347


def pick_port(preferred):
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main():
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    storage.init_db()
    _ensure_profile()
    storage.recompute_routes()

    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}"

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn
    from cycling.server import app

    print(f"{config.APP_NAME} — {url}")
    print(f"Data: {config.DB_PATH}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _ensure_profile():
    rider, bike = storage.get_profile()
    if rider is None or bike is None:
        from cycling import metrics
        r = dict(config.DEFAULT_RIDER)
        r["max_hr"] = metrics.default_max_hr(r["age"])
        r["hr_zones"] = metrics.default_hr_zones(r["max_hr"])
        storage.save_profile(r, dict(config.DEFAULT_BIKE))


if __name__ == "__main__":
    main()
