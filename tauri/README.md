# Native shell (optional)

The app is fully functional without this: `python main.py` (or `run.bat` /
`run.pyw` on Windows) starts the engine and opens the UI in your browser.

This folder wraps that same engine in a native Tauri window with a system tray
and optional autostart, per PLAN §10 ("Tauri (Rust) — native window, system
tray, autostart"). The shell does no data work of its own: on startup it
launches `main.py` on 127.0.0.1:8347 and points a webview at it.

## Building

Requires the Rust toolchain and the Tauri prerequisites for your OS
(see <https://v2.tauri.app/start/prerequisites/>):

```sh
cd tauri
npm install
npm run tauri build
```

The produced bundle expects `main.py` and the `cycling/` package to sit next
to the executable (bundled as resources) and a Python 3.9+ interpreter on
PATH.

> Note: the Python engine and web UI are the tested core of the app. The Rust
> shell is thin by design and can be rebuilt independently if the Tauri API
> changes between minor versions.
