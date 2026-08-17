//! Native shell for the Cycling Progress Tracker.
//!
//! The heavy lifting (FIT parsing, lidar, power model) lives in the Python
//! engine. This shell starts that engine on 127.0.0.1 and opens a native
//! window pointed at it, plus a system tray entry and optional autostart.

use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};

const PORT: u16 = 8347;

struct Engine {
    child: Option<Child>,
}

/// Launch the Python engine on the given port (without opening a browser).
fn spawn_engine() -> std::io::Result<Child> {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        Command::new("python")
            .args(["main.py", "--port", &PORT.to_string(), "--no-browser"])
            .current_dir(app_root())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
    }
    #[cfg(not(target_os = "windows"))]
    {
        Command::new("python3")
            .args(["main.py", "--port", &PORT.to_string(), "--no-browser"])
            .current_dir(app_root())
            .spawn()
    }
}

/// Absolute path to the directory that contains main.py.
fn app_root() -> std::path::PathBuf {
    let exe = std::env::current_exe().unwrap_or_default();
    let mut dir = exe.parent().map(|p| p.to_path_buf()).unwrap_or_default();
    // In dev the binary lives in target/debug; the app sources are a few
    // levels up. Walk up until we find main.py.
    for _ in 0..5 {
        if dir.join("main.py").exists() {
            return dir;
        }
        dir = dir.parent().map(|p| p.to_path_buf()).unwrap_or(dir);
    }
    dir
}

/// Poll the engine until it answers HTTP (timeout ~20 s).
fn wait_until_ready() {
    for _ in 0..100 {
        if std::net::TcpStream::connect(("127.0.0.1", PORT)).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        .setup(|app| {
            let state = Engine { child: spawn_engine().ok() };
            app.manage(Mutex::new(state));

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                wait_until_ready();
                if let Ok(url) = format!("http://127.0.0.1:{PORT}").parse::<WebviewUrl>() {
                    let _ = WebviewWindowBuilder::new(&handle, "main", url)
                        .title("Cycling Progress Tracker")
                        .inner_size(1240.0, 820.0)
                        .min_inner_size(900.0, 640.0)
                        .build();
                }
            });

            let quit = tauri::menu::MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let open = tauri::menu::MenuItem::with_id(app, "open", "Open", true, None::<&str>)?;
            let menu = tauri::menu::Menu::with_items(app, &[&open, &quit])?;
            let _tray = TrayIconBuilder::with_id("main-tray")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "quit" => app.exit(0),
                    "open" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { .. } = event {
                        let app = tray.app_handle();
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Close hides to the tray rather than quitting the engine.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Mutex<Engine>>() {
                    if let Ok(mut engine) = state.lock() {
                        if let Some(child) = engine.child.as_mut() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}
