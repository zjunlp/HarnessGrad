#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod diagnostics;
mod window_placement;

use diagnostics::Diagnostics;
use std::{
    fs,
    fs::OpenOptions,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tauri::{LogicalSize, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow};
use window_placement::{clamp_position, fit_client, fit_logical, parse_position, WorkArea};

struct PlacementState {
    point: (i32, i32),
    changed: Instant,
    pending: bool,
    closed: bool,
}

impl PlacementState {
    fn settle(&mut self, observed: Instant, point: Option<(i32, i32)>) -> bool {
        if self.closed {
            return true;
        }
        if self.changed != observed {
            return false;
        }
        if let Some(point) = point {
            self.point = point;
        }
        self.pending = false;
        true
    }
}

fn read_bounded(path: &Path, limit: usize) -> std::io::Result<Vec<u8>> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > limit as u64 {
        return Err(std::io::Error::other(
            "desktop configuration exceeds its file limit",
        ));
    }
    let mut raw = Vec::new();
    fs::File::open(path)?
        .take(limit as u64 + 1)
        .read_to_end(&mut raw)?;
    if raw.len() > limit {
        return Err(std::io::Error::other(
            "desktop configuration changed beyond its byte limit",
        ));
    }
    Ok(raw)
}

#[tauri::command]
async fn window_action(
    window: WebviewWindow,
    action: String,
    log: tauri::State<'_, Diagnostics>,
) -> Result<bool, String> {
    if !matches!(
        action.as_str(),
        "drag"
            | "minimize"
            | "close"
            | "focus"
            | "pin"
            | "bar"
            | "input"
            | "details"
            | "bubble"
            | "ordinary"
            | "status"
    ) {
        return Err("unknown window action".into());
    }
    log.record("command.started", serde_json::json!({"action":action}));
    let was_ordinary = window.is_decorated().unwrap_or(false);
    let result = match action.as_str() {
        "drag" => window.start_dragging(),
        "minimize" => window.minimize(),
        "close" => window.close(),
        "focus" => window.set_focus(),
        "pin" => window
            .is_always_on_top()
            .and_then(|v| window.set_always_on_top(!v)),
        "ordinary" => ordinary_window(&window),
        "status" => Ok(()),
        "bar" | "input" | "details" | "bubble" => {
            let (w, h) = match action.as_str() {
                "bar" => (268, 72),
                "input" => (382, 288),
                "bubble" => (382, 420),
                _ => (620, 700),
            };
            if window.is_decorated().unwrap_or(false) {
                Ok(())
            } else {
                resize_to(&window, (w, h))
            }
        }
        _ => return Err("unknown window action".into()),
    };
    log.record(
        "command.completed",
        serde_json::json!({"action":action,"ok":result.is_ok()}),
    );
    result
        .map(|_| {
            if action == "close" {
                was_ordinary
            } else {
                window.is_decorated().unwrap_or(false)
            }
        })
        .map_err(|_| "window operation unavailable on this desktop".into())
}

fn ordinary_window(window: &WebviewWindow) -> tauri::Result<()> {
    window.set_decorations(true)?;
    window.set_resizable(true)?;
    window.set_maximizable(true)?;
    window.set_always_on_top(false)?;
    window.set_size(LogicalSize::new(620.0, 700.0))?;
    // A desktop without absolute positioning keeps the OS-managed ordinary window.
    let _ = keep_visible(window);
    Ok(())
}

fn keep_visible(window: &WebviewWindow) -> tauri::Result<()> {
    if window.is_minimized()? {
        return Ok(());
    }
    let area = work_area(window)?;
    let outer = window.outer_size()?;
    let inner = window.inner_size()?;
    let frame = (
        outer.width.saturating_sub(inner.width),
        outer.height.saturating_sub(inner.height),
    );
    let fitted = fit_client((inner.width, inner.height), frame, area)
        .ok_or_else(|| std::io::Error::other("no usable desktop work area"))?;
    if fitted != (inner.width, inner.height) {
        window.set_size(PhysicalSize::new(fitted.0, fitted.1))?;
    }
    let size = window.outer_size()?;
    let point = window.outer_position()?;
    let target = clamp_position((point.x, point.y), (size.width, size.height), area)
        .ok_or_else(|| std::io::Error::other("window does not fit the desktop work area"))?;
    if target != (point.x, point.y) && !window.is_minimized()? {
        window.set_position(PhysicalPosition::new(target.0, target.1))?;
    }
    Ok(())
}

fn work_area(window: &WebviewWindow) -> tauri::Result<WorkArea> {
    let monitor = window
        .current_monitor()?
        .or(window.primary_monitor()?)
        .ok_or_else(|| std::io::Error::other("no graphical monitor available"))?;
    let rect = monitor.work_area();
    Ok(WorkArea {
        left: rect.position.x,
        top: rect.position.y,
        width: rect.size.width,
        height: rect.size.height,
    })
}

fn resize_to(window: &WebviewWindow, logical: (u32, u32)) -> tauri::Result<()> {
    let area = work_area(window)?;
    let outer = window.outer_size()?;
    let inner = window.inner_size()?;
    let frame = (
        outer.width.saturating_sub(inner.width),
        outer.height.saturating_sub(inner.height),
    );
    let fitted = fit_logical(logical, window.scale_factor()?, frame, area)
        .ok_or_else(|| std::io::Error::other("no usable desktop size"))?;
    window.set_size(PhysicalSize::new(fitted.0, fitted.1))?;
    keep_visible(window)
}

fn schedule_visibility(
    window: WebviewWindow,
    placement: Arc<Mutex<PlacementState>>,
    log: Diagnostics,
) {
    {
        let Ok(mut saved) = placement.lock() else {
            return;
        };
        saved.changed = Instant::now();
        if saved.pending || saved.closed {
            return;
        }
        saved.pending = true;
    }
    // At most one worker per window; wait until a drag settles before clamping.
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(200));
        let observed = {
            let Ok(saved) = placement.lock() else {
                return;
            };
            if saved.closed {
                return;
            }
            if saved.changed.elapsed() < Duration::from_millis(200) {
                continue;
            }
            saved.changed
        };
        // No cache lock is held while querying the event-loop dispatcher.
        let point = if window.is_minimized().unwrap_or(true) {
            None
        } else {
            if !window.is_decorated().unwrap_or(true) && keep_visible(&window).is_err() {
                log.record("window.placement_unavailable", serde_json::json!({}));
            }
            window
                .outer_position()
                .ok()
                .filter(|_| !window.is_minimized().unwrap_or(true))
                .map(|v| (v.x, v.y))
        };
        {
            let Ok(mut saved) = placement.lock() else {
                return;
            };
            if !saved.settle(observed, point) {
                continue;
            }
        }
        break;
    });
}

struct WindowSettings {
    url: tauri::Url,
    origin: String,
    script: String,
    position_file: PathBuf,
    webview_data: PathBuf,
    force_ordinary: bool,
}

fn create_window(
    handle: tauri::AppHandle,
    settings: WindowSettings,
    log: Diagnostics,
) -> tauri::Result<()> {
    let WindowSettings {
        url,
        origin,
        script,
        position_file,
        webview_data,
        force_ordinary,
    } = settings;
    log.record("window.build_started", serde_json::json!({}));
    let guard_origin = origin.clone();
    let window = tauri::WebviewWindowBuilder::new(&handle, "personal", WebviewUrl::External(url))
        .title("HarnessGrad · 个人持续进化")
        .inner_size(
            if force_ordinary { 620.0 } else { 268.0 },
            if force_ordinary { 700.0 } else { 72.0 },
        )
        .resizable(force_ordinary)
        .maximizable(force_ordinary)
        .decorations(force_ordinary)
        .transparent(false)
        .always_on_top(!force_ordinary)
        .focused(false)
        .data_directory(webview_data)
        .initialization_script(&script)
        .on_navigation(move |next| next.origin().ascii_serialization() == guard_origin)
        .build()?;
    log.record("window.build_completed", serde_json::json!({}));
    let restored = read_bounded(&position_file, 4096)
        .ok()
        .and_then(|v| parse_position(&v));
    if !force_ordinary {
        if let Some(point) = restored {
            let _ = window.set_position(PhysicalPosition::new(point.0, point.1));
        } else if let Ok(Some(monitor)) = window.current_monitor() {
            let size = window.outer_size()?;
            let origin = monitor.position();
            let _ = window.set_position(PhysicalPosition::new(
                origin.x + monitor.size().width.saturating_sub(size.width + 40) as i32,
                origin.y + 120,
            ));
        }
    }
    if keep_visible(&window).is_err() {
        log.record("window.placement_unavailable", serde_json::json!({}));
        ordinary_window(&window)?;
        log.record("window.ordinary_fallback", serde_json::json!({}));
    }
    if let (Ok(position), Ok(size), Ok(scale), Ok(topmost)) = (
        window.outer_position(),
        window.outer_size(),
        window.scale_factor(),
        window.is_always_on_top(),
    ) {
        let geometry = serde_json::json!({"position":[position.x,position.y],
            "size":[size.width,size.height],"scale_factor":scale,"always_on_top":topmost,
            "transparent":false,"ordinary":window.is_decorated().ok(),"visible":window.is_visible().ok(),
            "minimized":window.is_minimized().ok(),"focused":window.is_focused().ok(),
            "monitor_count":window.available_monitors().ok().map(|v| v.len())});
        let _ = fs::write(
            position_file.with_file_name("desktop-geometry.json"),
            geometry.to_string(),
        );
        log.record("window.ready", geometry);
    }
    let initial = window
        .outer_position()
        .map(|v| (v.x, v.y))
        .unwrap_or((0, 0));
    let placement = Arc::new(Mutex::new(PlacementState {
        point: initial,
        changed: Instant::now(),
        pending: false,
        closed: false,
    }));
    let event_log = log.clone();
    let event_window = window.clone();
    window.on_window_event(move |event| {
        match event {
            tauri::WindowEvent::Moved(point) => {
                event_log.record(
                    "window.moved",
                    serde_json::json!({"position":[point.x,point.y]}),
                );
                schedule_visibility(event_window.clone(), placement.clone(), event_log.clone());
            }
            tauri::WindowEvent::Resized(size) => {
                event_log.record(
                    "window.resized",
                    serde_json::json!({"size":[size.width,size.height]}),
                );
            }
            tauri::WindowEvent::Focused(focused) => {
                event_log.record("window.focused", serde_json::json!({"focused":focused}));
                if *focused {
                    schedule_visibility(event_window.clone(), placement.clone(), event_log.clone());
                }
            }
            tauri::WindowEvent::ScaleFactorChanged { .. } => {
                schedule_visibility(event_window.clone(), placement.clone(), event_log.clone());
            }
            tauri::WindowEvent::CloseRequested { .. } => {
                // Use event-fed data; never synchronously call window getters here.
                if let Ok(saved) = placement.lock() {
                    let _ = fs::write(
                        &position_file,
                        serde_json::to_vec(&saved.point).unwrap_or_default(),
                    );
                }
                event_log.record("window.close_requested", serde_json::json!({}));
            }
            tauri::WindowEvent::Destroyed => {
                if let Ok(mut saved) = placement.lock() {
                    saved.closed = true;
                }
                event_log.record("window.destroyed", serde_json::json!({}));
            }
            _ => {}
        }
    });
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("HarnessGrad personal: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let force_ordinary = std::env::args().any(|value| value == "--ordinary");
    let connection = match std::env::args().nth(1) {
        Some(path) => PathBuf::from(path),
        None => {
            // An explicit sidecar permits normal desktop launchers without argv.
            // It contains a path only, never the connection token or credentials.
            let pointer = std::env::current_exe()?.with_file_name("personal-connection.path");
            let bytes = read_bounded(&pointer, 16384)?;
            let path = PathBuf::from(std::str::from_utf8(&bytes)?.trim());
            if !path.is_absolute() {
                return Err("connection sidecar must select one absolute connection file".into());
            }
            path
        }
    };
    let _single_instance = OpenOptions::new()
        .create(true)
        .write(true)
        .open(connection.with_file_name("desktop.lock"))?;
    _single_instance
        .try_lock()
        .map_err(|_| "personal desktop is already running for this state directory")?;
    let bytes = read_bounded(&connection, 16384)?;
    let value: serde_json::Value = serde_json::from_slice(&bytes)?;
    let url: tauri::Url = value["url"]
        .as_str()
        .ok_or("missing service URL")?
        .parse()?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("expected the owned loopback service URL".into());
    }
    let token = value["token"]
        .as_str()
        .ok_or("missing local connection token")?;
    if token.len() != 64 || !token.bytes().all(|v| v.is_ascii_hexdigit()) {
        return Err("invalid token".into());
    }
    let origin = url.origin().ascii_serialization();
    let script = format!(
        "window.HG_CONNECTION={};",
        serde_json::json!({"token":token,"ordinary":force_ordinary})
    );
    let position_file = connection.with_file_name("window-position.json");
    let webview_data = connection.with_file_name("webview");
    let log = Diagnostics::new(connection.with_file_name("desktop-events.jsonl"));
    log.record(
        "desktop.starting",
        serde_json::json!({"pid":std::process::id()}),
    );
    let lifecycle_log = log.clone();
    let settings = WindowSettings {
        url,
        origin,
        script,
        position_file,
        webview_data,
        force_ordinary,
    };
    let app = tauri::Builder::default()
        .manage(log.clone())
        .invoke_handler(tauri::generate_handler![window_action])
        .setup(move |app| {
            let handle = app.handle().clone();
            // WebView2 creation and window getters must not block its UI/IPC callback.
            std::thread::spawn(move || {
                if let Err(error) = create_window(handle.clone(), settings, log.clone()) {
                    log.record("window.build_failed", serde_json::json!({"error_kind":format!("{:?}", std::mem::discriminant(&error))}));
                    handle.exit(1);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())?;
    app.run(move |_handle, event| match event {
        tauri::RunEvent::ExitRequested { code, .. } => {
            lifecycle_log.record("desktop.exit_requested", serde_json::json!({"code":code}))
        }
        tauri::RunEvent::Exit => lifecycle_log.record("desktop.exited", serde_json::json!({})),
        _ => {}
    });
    Ok(())
}

#[cfg(test)]
mod startup_tests {
    use super::*;

    #[test]
    fn placement_preserves_visible_position_across_stale_and_minimized_samples() {
        let old = Instant::now();
        let current = old + Duration::from_millis(1);
        let mut saved = PlacementState {
            point: (234, 234),
            changed: current,
            pending: true,
            closed: false,
        };
        assert!(!saved.settle(old, Some((-32000, -32000))));
        assert!(saved.pending);
        assert_eq!(saved.point, (234, 234));
        // No visible sample is available while minimized: stop, preserving restore data.
        assert!(saved.settle(current, None));
        assert!(!saved.pending);
        assert_eq!(saved.point, (234, 234));
        saved.pending = true;
        assert!(saved.settle(current, Some((420, 280))));
        assert!(!saved.pending);
        assert_eq!(saved.point, (420, 280));
    }

    #[test]
    fn selected_loopback_page_can_control_its_personal_window() {
        let mut context: tauri::Context<tauri::Wry> = tauri::generate_context!();
        let origin = tauri::ipc::Origin::Remote {
            url: "http://127.0.0.1:37421/".parse().unwrap(),
        };
        assert!(context
            .runtime_authority_mut()
            .resolve_access("window_action", "personal", "personal", &origin)
            .is_some());
    }

    #[test]
    fn window_control_does_not_grant_other_origins_windows_or_commands() {
        let mut context: tauri::Context<tauri::Wry> = tauri::generate_context!();
        let authority = context.runtime_authority_mut();
        for (command, window, url) in [
            ("window_action", "personal", "https://example.invalid/"),
            ("window_action", "personal", "http://localhost:37421/"),
            ("window_action", "other", "http://127.0.0.1:37421/"),
            ("unknown_command", "personal", "http://127.0.0.1:37421/"),
        ] {
            let origin = tauri::ipc::Origin::Remote {
                url: url.parse().unwrap(),
            };
            assert!(authority
                .resolve_access(command, window, window, &origin)
                .is_none());
        }
    }

    #[test]
    fn configuration_reads_enforce_the_selected_byte_limit() {
        use std::time::{SystemTime, UNIX_EPOCH};
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("hg-a5-config-{}-{nonce}.txt", std::process::id()));
        fs::write(&path, b"synthetic-config").unwrap();
        assert_eq!(read_bounded(&path, 32).unwrap(), b"synthetic-config");
        assert!(read_bounded(&path, 4).is_err());
    }
}
