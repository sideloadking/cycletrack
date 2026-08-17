# Preview run doc

Python/FastAPI app (no Node). The web UI (`web/`) is served by the FastAPI
engine itself — there is no separate frontend build.

## Reproduce artifacts

- No env files, build steps, or generated assets needed.
- Data root: `~/.cycling_tracker` (override with `CYCLING_DATA_ROOT` if you
  want a scratch DB). `main.py` calls `storage.init_db()` and creates a
  default profile on first run — idempotent.
- Dependencies live in the repo venv: `.venv/Scripts/python.exe` (uvicorn,
  fastapi, numpy, scipy, requests). Do NOT use the system Python — it lacks
  uvicorn (the earlier preview failure in `.preview_server.log`).

## Run the server

```bash
.venv/Scripts/python.exe main.py --port 8347 --no-browser
```

- Default port is 8347; `main.py` scans upward for a free one if busy.
- `--no-browser` skips the auto-open so the preview can drive it.
- Detached start (Windows): use `Start-Process` with the venv python.exe and
  redirect stdout/stderr to different files.
