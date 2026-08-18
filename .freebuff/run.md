# Cycling Progress Tracker — preview run doc

## Reproduce the artifacts a fresh checkout needs

No per-worktree env files or secrets are required. The app needs no API keys —
it talks to public OpenStreetMap / Open-Meteo / EA LIDAR endpoints. Its data
(SQLite DB + caches) lives outside the repo at `~/.cycling_tracker`
(override with `CYCLING_DATA_ROOT`); it is created automatically on startup.

Dependencies are the only artifact to install, into the project venv:

```sh
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

(`requirements.txt` is the single dependency manifest; the current venv
already has them installed.)

## Run the server

The engine is a FastAPI app served by uvicorn on 127.0.0.1. Default port is
**8347** (pass `--port` to change). Use `--no-browser` when serving for a
preview.

```sh
.venv/Scripts/python.exe main.py --port 8347 --no-browser
```

Detached on Windows (PowerShell; stdout and stderr must go to different
files):

```
powershell -NoProfile -Command "(Start-Process -FilePath 'E:\cycling\.venv\Scripts\python.exe' -ArgumentList 'main.py','--port','8347','--no-browser' -WorkingDirectory 'E:\cycling' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"
```

UI is served at http://127.0.0.1:8347/.
