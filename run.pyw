"""Windowless Windows launcher — double-click to start the app.

Uses .pyw so no console window appears; the engine logs are quiet by default.
If the project's .venv exists, re-run under it (its dependencies are known
good); otherwise fall back to whatever Python owns .pyw files.
"""

import os
import subprocess
import sys

_THIS = os.path.abspath(__file__)
_VENV = os.path.join(os.path.dirname(_THIS), ".venv", "Scripts", "python.exe")
if os.path.exists(_VENV) and os.path.abspath(sys.executable) != os.path.abspath(_VENV):
    sys.exit(subprocess.call([_VENV, _THIS] + sys.argv[1:]))

import threading
import webbrowser

from cycling import config, storage
import main as app_main


def _open(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    storage.init_db()
    app_main._ensure_profile()
    port = app_main.pick_port(app_main.DEFAULT_PORT)
    url = f"http://127.0.0.1:{port}"
    threading.Timer(1.2, lambda: _open(url)).start()

    import uvicorn
    from cycling.server import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
