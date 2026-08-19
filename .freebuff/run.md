# Preview run doc — Cycling Progress Tracker

FastAPI engine (Python) serving the static UI in `web/`. There is no Node build
step; the browser UI is plain JS served by the engine.

## Reproduce the artifacts

- **No env files are required.** The only environment knob the preview uses is
  `CYCLING_DATA_ROOT`, which points the engine at a *preview-local* data root
  so the preview never touches the user's real data
  (`~/.cycling_tracker`). The project's gitignore already ignores
  `.preview_data/`.
- If `.preview_data/` is missing (fresh checkout), create it by simply running
  the server with `CYCLING_DATA_ROOT` set — `cycling/config.py` creates the
  data root, `cache/`, and `imports/` dirs on import. The dashboard then
  starts empty; to seed it, drop a `.fit` file into
  `.preview_data/imports/` or import one through the UI's import dialog.
- Dependencies live in the repo's `.venv/` (Python 3.12). If the venv is
  missing, recreate it and install `requirements.txt`:
  ```
  python -m venv .venv
  .venv/Scripts/pip install -r requirements.txt
  ```
- Do **not** copy or touch `cycling.preview_data/` (a stray empty dir) or the
  real `~/.cycling_tracker` data.

## Run the server

Default port is **8347** (free at time of writing). Start it detached with
PowerShell, setting `CYCLING_DATA_ROOT` in the parent so the child inherits it
(Start-Process in Windows PowerShell 5.1 has no `-Environment` parameter):

```
powershell -NoProfile -Command "$env:CYCLING_DATA_ROOT='E:\cycling\.preview_data'; (Start-Process -FilePath 'E:\cycling\.venv\Scripts\python.exe' -ArgumentList 'main.py','--port','8347','--no-browser' -WorkingDirectory 'E:\cycling' -RedirectStandardOutput 'E:\cycling\.freebuff\preview.log' -RedirectStandardError 'E:\cycling\.freebuff\preview.err' -WindowStyle Hidden -PassThru).Id"
```

(stdout and stderr must go to different files.) Verify the process survived,
then wait for the health endpoint:

```
powershell -NoProfile -Command "Get-Process -Id <pid>"
curl -s http://127.0.0.1:8347/api/health
```

The app is served at <http://127.0.0.1:8347> (UI) with a REST API under
`/api/*` (`/api/health`, `/api/overview`, `/api/rides`, ...). Use
`--no-browser` when previewing; omit it when the user double-clicks
`run.bat`/`run.pyw` themselves.
