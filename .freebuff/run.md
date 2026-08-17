# VeloTrack preview runbook

## Reproduce artifacts

- This checkout has no required `.env` or other ignored frontend artifact; the app uses its local default data root.
- For a fresh checkout, create the project virtual environment with `python -m venv .venv` and install the pinned project requirements with `.venv\\Scripts\\python.exe -m pip install -r requirements.txt`.
- If a worktree contains a future `.env` file, copy the file from the main checkout rather than symlinking it; keep secrets out of this document.

## Run the server

- From `E:\\cycling`, run `.venv\\Scripts\\python.exe main.py --no-browser --port 8347`.
- The engine serves the static frontend and API at `http://127.0.0.1:8347`; `main.py` automatically advances to the next free port if the requested port is occupied.
- The Freebuff preview uses a detached PowerShell process and writes stdout/stderr to the thread-specific files under `.freebuff/`.
