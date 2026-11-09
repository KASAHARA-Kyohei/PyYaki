use crate::process::Worker;
use pyyaki_host::navigation::NavigationGate;
use pyyaki_host::startup::{Startup, Update, validate_shell_progress};
use pyyaki_host::{MAX_MESSAGE_BYTES, ORIGIN, VERSION, assets, config::Config, framing};
use serde_json::{Value, json};
use std::{
    error::Error,
    fs,
    io::Write,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc::{self, SyncSender},
    },
    thread,
    time::{Duration, Instant},
};
#[cfg(windows)]
use tao::platform::windows::WindowExtWindows;
use tao::{
    dpi::LogicalSize,
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy},
    window::{Window, WindowBuilder},
};
#[cfg(windows)]
use windows::core::PCWSTR;
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::RECT,
    Graphics::Gdi::{
        CreateSolidBrush, DT_CENTER, DT_NOPREFIX, DT_WORDBREAK, DeleteObject, DrawTextW, FillRect,
        GetDC, ReleaseDC, SetBkMode, SetTextColor, TRANSPARENT,
    },
    UI::WindowsAndMessaging::{GetClientRect, MB_ICONERROR, MB_OK, MessageBoxW},
};
use wry::{WebContext, WebView, WebViewBuilder, http::Response};

#[cfg(windows)]
use wry::{WebViewBuilderExtWindows, WebViewExtWindows};

enum HostEvent {
    Ipc(QueuedMessage<String>),
    Spawned(Worker),
    BackendReady,
    StartupPhase(&'static str),
    Reply(QueuedMessage<Value>),
    Failed(String),
}

struct StartupLog {
    file: Mutex<fs::File>,
    started: Instant,
    diagnostics: bool,
}

struct BackendPaths {
    python: PathBuf,
    root: PathBuf,
    log: PathBuf,
}

fn ui_window_state(window: &Window, webview: &WebView) -> Value {
    #[cfg(not(target_os = "macos"))]
    let _ = webview;
    #[allow(unused_mut)]
    let mut state = json!({"visible":window.is_visible(),"focused":window.is_focused(),
        "minimized":window.is_minimized()});
    #[cfg(target_os = "macos")]
    if let Some(mtm) = objc2::MainThreadMarker::new() {
        use wry::WebViewExtMacOS;
        let application = objc2_app_kit::NSApplication::sharedApplication(mtm);
        state["application_active"] = json!(application.isActive());
        state["application_hidden"] = json!(application.isHidden());
        let view = webview.webview();
        state["webview_hidden"] = json!(view.isHiddenOrHasHiddenAncestor());
        let rect = view.visibleRect();
        state["webview_visible_size"] = json!([rect.size.width, rect.size.height]);
        if let Some(attached) = view.window() {
            state["window_occlusion_bits"] = json!(attached.occlusionState().bits());
            state["window_unoccluded"] = json!(
                attached
                    .occlusionState()
                    .contains(objc2_app_kit::NSWindowOcclusionState::Visible)
            );
            state["window_on_active_space"] = json!(attached.isOnActiveSpace());
            state["window_collection_behavior_bits"] = json!(attached.collectionBehavior().bits());
        }
    }
    state
}

impl StartupLog {
    fn mark(&self, phase: &str, data: Value) {
        let record = json!({"phase":phase,
            "host_elapsed_ms":self.started.elapsed().as_secs_f64() * 1000.0,"data":data});
        self.line(&format!("pyyaki_startup={record}"));
        if self.diagnostics {
            eprintln!("pyyaki_diagnostic={record}");
        }
    }

    fn line(&self, text: &str) {
        if let Ok(mut file) = self.file.lock() {
            let _ = writeln!(file, "{text}");
            let _ = file.flush();
        }
    }
}

struct QueuedMessage<T> {
    value: T,
    count: Arc<AtomicUsize>,
}
impl<T> QueuedMessage<T> {
    fn new(value: T, count: &Arc<AtomicUsize>, limit: usize) -> Result<Self, &'static str> {
        count
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                (count < limit).then_some(count + 1)
            })
            .map_err(|_| "Native message queue limit exceeded")?;
        Ok(Self {
            value,
            count: count.clone(),
        })
    }
}
impl<T> Drop for QueuedMessage<T> {
    fn drop(&mut self) {
        self.count.fetch_sub(1, Ordering::AcqRel);
    }
}

fn token() -> Result<String, Box<dyn Error>> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("OS randomness failed: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(windows)]
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain([0]).collect()
}

#[cfg(windows)]
pub fn show_error(message: &str) {
    // SAFETY: null owner is allowed, both UTF-16 strings live through the modal API call.
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            wide(message).as_ptr(),
            wide("PyYaki").as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}

#[cfg(windows)]
pub(crate) fn paint(window: &Window, message: &str) {
    let hwnd = window.hwnd() as _;
    // SAFETY: tao owns hwnd on this GUI thread; DC and brush are released exactly once.
    unsafe {
        let dc = GetDC(hwnd);
        if dc.is_null() {
            return;
        }
        let mut rect = RECT::default();
        GetClientRect(hwnd, &mut rect);
        let brush = CreateSolidBrush(0x00eef5f8);
        if !brush.is_null() {
            FillRect(dc, &rect, brush);
            DeleteObject(brush);
        }
        rect.left += 32;
        rect.right -= 32;
        rect.top = rect.bottom / 3;
        SetBkMode(dc, TRANSPARENT as i32);
        SetTextColor(dc, 0x002f3828);
        let text = wide(message);
        DrawTextW(
            dc,
            text.as_ptr(),
            (text.len() - 1) as i32,
            &mut rect,
            DT_CENTER | DT_WORDBREAK | DT_NOPREFIX,
        );
        ReleaseDC(hwnd, dc);
    }
}

#[cfg(windows)]
fn post(view: &WebView, value: &Value) -> Result<(), Box<dyn Error>> {
    let text = wide(&serde_json::to_string(value)?);
    // SAFETY: WebView2 is used on its creating STA thread and copies the valid JSON string.
    unsafe {
        view.webview().PostWebMessageAsJson(PCWSTR(text.as_ptr()))?;
    }
    Ok(())
}

fn start_backend(
    paths: BackendPaths,
    session: String,
    receiver: mpsc::Receiver<String>,
    proxy: EventLoopProxy<HostEvent>,
    closing: Arc<AtomicBool>,
    trace: Arc<StartupLog>,
) {
    thread::spawn(move || {
        let mut stage = "python_spawn";
        let result = (|| -> Result<(), Box<dyn Error>> {
            trace.mark("python_spawn_started", Value::Null);
            let worker = Worker::spawn(&paths.python, &paths.root, &paths.log)?;
            #[cfg(target_os = "macos")]
            let spawned = json!({"worker_pid":worker.pid(), "guard_pid":worker.guard_pid(),
                "process_group":worker.guard_pid()});
            #[cfg(windows)]
            let spawned = Value::Null;
            trace.mark("python_spawned", spawned);
            if closing.load(Ordering::Acquire) {
                return Ok(());
            }
            let mut reader = worker.read.try_clone()?;
            let mut writer = worker.write.try_clone()?;
            proxy
                .send_event(HostEvent::Spawned(worker))
                .map_err(|_| "GUI closed")?;
            let nonce = token()?;
            stage = "worker_handshake";
            framing::write(
                &mut writer,
                &json!({"v":1,"type":"worker_hello", "nonce":nonce,
                "framework_version":VERSION}),
            )?;
            let mut startup = Startup::default();
            loop {
                match startup.accept(framing::read(&mut reader)?, &nonce)? {
                    Update::Progress { phase, elapsed_ms } => {
                        stage = phase;
                        trace.mark(phase, json!({"worker_elapsed_ms":elapsed_ms}));
                        proxy
                            .send_event(HostEvent::StartupPhase(phase))
                            .map_err(|_| "GUI closed")?;
                    }
                    Update::Ready => break,
                    Update::Failed(message) => return Err(message.into()),
                }
            }
            stage = "connection";
            trace.mark("worker_welcome", Value::Null);
            let writer_proxy = proxy.clone();
            thread::spawn(move || {
                for text in receiver {
                    if framing::write_text(&mut writer, &text).is_err() {
                        let _ = writer_proxy
                            .send_event(HostEvent::Failed("Worker pipe disconnected".into()));
                        break;
                    }
                }
            });
            proxy
                .send_event(HostEvent::BackendReady)
                .map_err(|_| "GUI closed")?;
            let pending_replies = Arc::new(AtomicUsize::new(0));
            loop {
                let mut frame = framing::read(&mut reader)?;
                if frame.get("type") == Some(&json!("worker_failed")) {
                    return match startup.accept(frame, &nonce)? {
                        Update::Failed(message) => Err(message.into()),
                        _ => Err("Invalid worker failure frame".into()),
                    };
                }
                if frame.get("v") != Some(&json!(1))
                    || frame.get("type") != Some(&json!("dispatch"))
                    || frame.get("session_id") != Some(&json!(session))
                    || frame.as_object().is_none_or(|o| o.len() != 4)
                {
                    return Err("Invalid worker dispatch frame".into());
                }
                proxy
                    .send_event(HostEvent::Reply(QueuedMessage::new(
                        frame["message"].take(),
                        &pending_replies,
                        64,
                    )?))
                    .map_err(|_| "GUI closed")?;
            }
        })();
        if let Err(error) = result
            && !closing.load(Ordering::Acquire)
        {
            trace.mark(
                "worker_failed",
                json!({"stage":stage,"message":error.to_string()}),
            );
            // The stage is retained in the trace and rendered separately in the UI.
            let _ = proxy.send_event(HostEvent::Failed(error.to_string()));
        }
    });
}

fn send(sender: &SyncSender<String>, session: &str, public: &str) -> Result<(), Box<dyn Error>> {
    // Preserve original public JSON for Python's duplicate-key and strict field checks.
    let text =
        format!(r#"{{"v":1,"type":"dispatch","session_id":"{session}","message":{public}}}"#);
    if text.len() > MAX_MESSAGE_BYTES {
        return Err("IPC frame exceeds 1 MiB".into());
    }
    sender
        .try_send(text)
        .map_err(|_| "Worker input queue is full or disconnected".into())
}

fn diagnostic(enabled: bool, started: Instant, phase: &str, data: Value) {
    if enabled {
        eprintln!(
            "pyyaki_diagnostic={}",
            json!({
                "phase": phase, "host_elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
                "data": data,
            })
        );
    }
}

#[cfg(target_os = "macos")]
static INTERRUPTED: AtomicBool = AtomicBool::new(false);
#[cfg(target_os = "macos")]
extern "C" fn interrupt(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::Relaxed);
}

pub fn run() -> Result<(), Box<dyn Error>> {
    run_with_args(std::env::args_os().skip(1))
}

pub fn run_with_args(
    arguments: impl IntoIterator<Item = std::ffi::OsString>,
) -> Result<(), Box<dyn Error>> {
    #[cfg(target_os = "macos")]
    // SAFETY: handlers only store to a lock-free atomic; no allocation, locks or UI calls.
    unsafe {
        libc::signal(libc::SIGINT, interrupt as libc::sighandler_t);
        libc::signal(libc::SIGTERM, interrupt as libc::sighandler_t);
    }
    let started_at = Instant::now();
    let mut args = arguments.into_iter();
    let first = args.next();
    #[cfg(windows)]
    if first.as_deref() == Some(std::ffi::OsStr::new("--onefile-format")) {
        println!("PYYAKI01");
        return Ok(());
    }
    if first.as_deref() == Some(std::ffi::OsStr::new("--version")) {
        println!("PyYaki native host {VERSION}");
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    let packaged = if first.is_none()
        || matches!(
            first.as_deref().and_then(|s| s.to_str()),
            Some("--smoke-test" | "--diagnostics")
        ) {
        Some(crate::bundle::paths(&std::env::current_exe()?)?)
    } else {
        None
    };
    #[cfg(not(target_os = "macos"))]
    let packaged: Option<(PathBuf, PathBuf)> = None;
    if first.as_deref() != Some(std::ffi::OsStr::new("run")) && packaged.is_none() {
        return Err("Usage: pyyaki run --project <project> --python <venv-python>".into());
    }
    let mut smoke_test =
        packaged.is_some() && first.as_deref() == Some(std::ffi::OsStr::new("--smoke-test"));
    let mut diagnostics =
        packaged.is_some() && first.as_deref() == Some(std::ffi::OsStr::new("--diagnostics"));
    let mut timer_probe_seconds = None;
    #[cfg(target_os = "macos")]
    let development_host = packaged.is_none();
    let (mut root, mut python) = match packaged {
        Some((root, python)) => (root, Some(python)),
        None => (std::env::current_dir()?, None),
    };
    while let Some(arg) = args.next() {
        match arg.to_str() {
            Some("--smoke-test") => smoke_test = true,
            Some("--diagnostics") => diagnostics = true,
            Some("--timer-probe-seconds") => {
                let seconds: u64 = args
                    .next()
                    .ok_or("Missing timer probe seconds")?
                    .to_str()
                    .ok_or("Invalid timer probe seconds")?
                    .parse()?;
                if !cfg!(target_os = "macos") || !(15..=60).contains(&seconds) {
                    return Err("Timer probe requires macOS and 15..60 seconds per phase".into());
                }
                timer_probe_seconds = Some(seconds);
            }
            Some("--project") => root = PathBuf::from(args.next().ok_or("Missing project path")?),
            Some("--python") => {
                python = Some(std::path::absolute(PathBuf::from(
                    args.next().ok_or("Missing Python path")?,
                ))?)
            }
            _ => return Err("Unknown native argument".into()),
        }
    }
    if smoke_test && diagnostics {
        return Err("Choose either --smoke-test or --diagnostics".into());
    }
    if timer_probe_seconds.is_some() && !diagnostics {
        return Err("Timer probe requires --diagnostics".into());
    }
    let python = python.ok_or("Specify --python with the installed PyYaki development venv")?;
    let config = Arc::new(Config::load(&root)?);
    let session = token()?;
    let instance = instance_base()?;
    let instance = instance.join("PyYaki").join("prototype").join(&session);
    fs::create_dir_all(&instance)?;
    let host_log = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(instance.join("host.log"))?;
    let trace = Arc::new(StartupLog {
        file: Mutex::new(host_log),
        started: started_at,
        diagnostics,
    });
    trace.mark("host_started", json!({"pid":std::process::id()}));
    let event_loop = EventLoopBuilder::<HostEvent>::with_user_event().build();
    let builder = WindowBuilder::new()
        .with_title(&config.window.title)
        .with_inner_size(LogicalSize::new(config.window.width, config.window.height));
    #[cfg(windows)]
    let builder = {
        use tao::{
            dpi::PhysicalSize,
            platform::windows::{IconExtWindows, WindowBuilderExtWindows},
            window::Icon,
        };
        builder
            .with_window_icon(Some(Icon::from_resource(1, None)?))
            .with_taskbar_icon(Some(Icon::from_resource(
                1,
                Some(PhysicalSize::new(256, 256)),
            )?))
    };
    let window = builder.build(&event_loop)?;
    #[cfg(target_os = "macos")]
    let mut initial_space_behavior = {
        let (original, applied) = crate::startup_macos::prepare(&window);
        trace.mark(
            "macos_initial_space_behavior",
            json!({"action":"applied","original_bits":original.bits(),"applied_bits":applied.bits()}),
        );
        Some(original)
    };
    let proxy = event_loop.create_proxy();
    #[cfg(target_os = "macos")]
    let mut timer_probe = if let Some(seconds) = timer_probe_seconds {
        let control = WindowBuilder::new()
            .with_title("PyYaki timer probe control")
            .with_inner_size(LogicalSize::new(300, 160))
            .with_visible(false)
            .with_focused(false)
            .build(&event_loop)?;
        Some(crate::timer_probe::TimerProbe::new(control, seconds))
    } else {
        None
    };
    let closing = Arc::new(AtomicBool::new(false));
    let mut context = WebContext::new(Some(instance.join("profile")));
    let mut view: Option<WebView> = None;
    let mut worker: Option<Worker> = None;
    let mut input: Option<SyncSender<String>> = None;
    let mut pending_hello: Option<String> = None;
    let mut started = false;
    let mut sdk_connect_started = false;
    let mut shell_observations = 0;
    let mut next_ui_sample = Instant::now();
    let mut backend_ready = false;
    let mut failed = false;
    let mut last_worker_phase = "ui_initialization";
    let mut deadline: Option<Instant> = None;
    let mut shutdown_at: Option<Instant> = None;
    let mut exit_code = 0;
    let mut splash = "PyYaki — preparing native UI…".to_string();
    window.request_redraw();
    event_loop.run(move |event, _, flow| {
        *flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100));
        let result = (|| -> Result<(), Box<dyn Error>> {
            match event {
                Event::RedrawRequested(_) if view.is_none() => {
                    paint(&window, &splash);
                    if failed {
                        return Ok(());
                    }
                    trace.mark("native_draw", Value::Null);
                    #[cfg(target_os = "macos")]
                    if development_host {
                        crate::icon_macos::set_development_icon()?;
                    }
                    let protocol_config = config.clone();
                    let navigation = NavigationGate::default();
                    let ipc_proxy = proxy.clone();
                    let ipc_count = Arc::new(AtomicUsize::new(0));
                    let ipc_overflow = AtomicBool::new(false);
                    let active_assets = Arc::new(AtomicUsize::new(0));
                    let asset_trace = trace.clone();
                    let seen_assets = AtomicUsize::new(0);
                    let download_trace = trace.clone();
                    let navigation_trace = trace.clone();
                    let popup_trace = trace.clone();
                    let ipc_trace = trace.clone();
                    let download_seen = AtomicBool::new(false);
                    let navigation_seen = AtomicBool::new(false);
                    let popup_seen = AtomicBool::new(false);
                    let ipc_rejections = AtomicUsize::new(0);
                    let new_view =
                        platform_builder(WebViewBuilder::new_with_web_context(&mut context))
                            .with_devtools(false)
                            .with_download_started_handler(move |_, _| {
                                if !download_seen.swap(true, Ordering::AcqRel) {
                                    download_trace.mark("download_rejected", Value::Null);
                                }
                                false
                            })
                            .with_navigation_handler(move |url| {
                                let allowed = navigation.allow(&url);
                                if !allowed && !navigation_seen.swap(true, Ordering::AcqRel) {
                                    navigation_trace.mark("navigation_rejected", Value::Null);
                                }
                                allowed
                            })
                            .with_new_window_req_handler(move |_, _| {
                                if !popup_seen.swap(true, Ordering::AcqRel) {
                                    popup_trace.mark("new_window_rejected", Value::Null);
                                }
                                wry::NewWindowResponse::Deny
                            })
                            .with_initialization_script(bootstrap())
                            .with_initialization_script(if smoke_test {
                                include_str!("../smoke.js")
                            } else {
                                ""
                            })
                            .with_ipc_handler(move |request| {
                                if request.uri().to_string() == format!("{ORIGIN}/")
                                    && request.body().len() <= MAX_MESSAGE_BYTES
                                {
                                    if let Ok(message) =
                                        QueuedMessage::new(request.body().clone(), &ipc_count, 32)
                                    {
                                        let _ = ipc_proxy.send_event(HostEvent::Ipc(message));
                                    } else if !ipc_overflow.swap(true, Ordering::AcqRel) {
                                        let _ = ipc_proxy.send_event(HostEvent::Failed(
                                            "Native IPC queue limit exceeded".into(),
                                        ));
                                    }
                                } else {
                                    // Record each fixed reason once, without URLs or message bodies.
                                    let (bit, reason) =
                                        if request.uri().to_string() != format!("{ORIGIN}/") {
                                            (1, "origin")
                                        } else {
                                            (2, "message_too_large")
                                        };
                                    if ipc_rejections.fetch_or(bit, Ordering::AcqRel) & bit == 0 {
                                        ipc_trace
                                            .mark("native_ipc_rejected", json!({"reason":reason}));
                                    }
                                }
                            })
                            .with_asynchronous_custom_protocol(
                                "pyyaki".into(),
                                move |_, request, responder| {
                                    let traced_asset = match request.uri().path() {
                                        "/" => Some(("document", 1)),
                                        "/_pyyaki/sdk.js" => Some(("sdk", 2)),
                                        _ => None,
                                    }
                                    .filter(|(_, bit)| {
                                        seen_assets.fetch_or(*bit, Ordering::AcqRel) & bit == 0
                                    })
                                    .map(|(asset, _)| asset);
                                    if let Some(asset) = traced_asset {
                                        asset_trace.mark("asset_requested", json!({"asset":asset}));
                                    }
                                    if active_assets.fetch_add(1, Ordering::AcqRel) >= 16 {
                                        active_assets.fetch_sub(1, Ordering::AcqRel);
                                        responder.respond(
                                            Response::builder()
                                                .status(503)
                                                .body(Vec::new())
                                                .unwrap(),
                                        );
                                        return;
                                    }
                                    let active = active_assets.clone();
                                    let config = protocol_config.clone();
                                    let trace = asset_trace.clone();
                                    thread::spawn(move || {
                                        let response = if request.method() == "GET"
                                            && request.uri().scheme_str() == Some("pyyaki")
                                            && request.uri().host() == Some("localhost")
                                        {
                                            assets::resolve(
                                                &config.ui_dir,
                                                &config.ui_entry,
                                                request.uri().path(),
                                            )
                                        } else {
                                            Err(std::io::Error::other("Rejected asset request"))
                                        };
                                        let (status, body, mime) = match response {
                                            Ok((body, mime)) => (200, body, mime),
                                            Err(_) => (404, Vec::new(), "text/plain"),
                                        };
                                        if let Some(asset) = traced_asset {
                                            trace.mark(
                                                "asset_response",
                                                json!({"asset":asset,
                                                "status":status,"bytes":body.len()}),
                                            );
                                        }
                                        responder.respond(
                                            Response::builder()
                                                .status(status)
                                                .header("Content-Type", mime)
                                                .header("Content-Security-Policy", assets::CSP)
                                                .header("X-Content-Type-Options", "nosniff")
                                                .header("Cache-Control", "no-store")
                                                .body(body)
                                                .unwrap(),
                                        );
                                        active.fetch_sub(1, Ordering::AcqRel);
                                    });
                                },
                            )
                            .with_url("pyyaki://localhost/")
                            .build(&window)?;
                    view = Some(new_view);
                    trace.mark("webview_created", Value::Null);
                    deadline = Some(Instant::now() + Duration::from_secs(10));
                    trace.mark("ui_deadline_started", json!({"timeout_ms":10000}));
                }
                Event::UserEvent(HostEvent::Ipc(mut incoming)) if shutdown_at.is_none() => {
                    let text = std::mem::take(&mut incoming.value);
                    drop(incoming);
                    let message: Value = serde_json::from_str(&text)?;
                    if diagnostics && message.get("type") == Some(&json!("pyyaki_diagnostic")) {
                        if message.get("v") != Some(&json!(1))
                            || !matches!(
                                message.get("phase").and_then(Value::as_str),
                                Some(
                                    "ready"
                                        | "sample"
                                        | "timer_probe_ready"
                                        | "timer_phase_seen"
                                        | "interactive"
                                        | "fault_ready"
                                        | "paused_timeout"
                                        | "result"
                                )
                            )
                            || message.as_object().is_none_or(|object| object.len() != 4)
                            || message.get("data").is_none()
                        {
                            return Err("Invalid diagnostic marker".into());
                        }
                        let phase = message["phase"].as_str().unwrap();
                        if matches!(phase, "timer_probe_ready" | "timer_phase_seen")
                            && timer_probe_seconds.is_none()
                        {
                            return Err("Timer probe flag missing".into());
                        }
                        #[cfg(target_os = "macos")]
                        if phase == "timer_probe_ready" {
                            let probe = timer_probe.as_mut().ok_or("Timer probe flag missing")?;
                            if message["data"] != json!({}) {
                                return Err("Invalid timer probe marker".into());
                            }
                            let (name, data) = probe.start(
                                &window,
                                view.as_ref().ok_or("No WebView")?,
                                started_at,
                            )?;
                            diagnostic(true, started_at, name, data);
                        }
                        diagnostic(true, started_at, phase, message["data"].clone());
                        if phase == "result" {
                            exit_code = if message["data"]["ok"] == json!(true) {
                                0
                            } else {
                                1
                            };
                            closing.store(true, Ordering::Release);
                            if let Some(sender) = &input {
                                let _ = sender.try_send(r#"{"v":1,"type":"shutdown"}"#.into());
                            }
                            shutdown_at = Some(Instant::now() + Duration::from_secs(2));
                        }
                    } else if failed {
                        return Ok(());
                    } else if smoke_test && message.get("type") == Some(&json!("pyyaki_smoke_done"))
                    {
                        exit_code = if message.get("ok") == Some(&json!(true)) {
                            0
                        } else {
                            1
                        };
                        trace.line(&format!("native_smoke={message}"));
                        eprintln!("native_smoke={message}");
                        closing.store(true, Ordering::Release);
                        if let Some(sender) = &input {
                            let _ = sender.try_send(r#"{"v":1,"type":"shutdown"}"#.into());
                        }
                        shutdown_at = Some(Instant::now() + Duration::from_secs(2));
                    } else if message == json!({"v":1,"type":"pyyaki_connect_started"})
                        && !started
                        && !sdk_connect_started
                    {
                        sdk_connect_started = true;
                        trace.mark("sdk_connect_started", Value::Null);
                    } else if message.get("type") == Some(&json!("pyyaki_shell_progress")) {
                        validate_shell_progress(&message)?;
                        if shell_observations < 16 {
                            shell_observations += 1;
                            trace.mark(
                                "sdk_shell_progress",
                                json!({"stage":message["stage"],
                                "visibility":message["visibility"],"focused":message["focused"]}),
                            );
                        }
                    } else if message == json!({"v":1,"type":"pyyaki_shell_ready"}) && !started {
                        started = true;
                        trace.mark("shell_marker", Value::Null);
                        #[cfg(target_os = "macos")]
                        if let Some(bits) =
                            crate::startup_macos::restore(&window, &mut initial_space_behavior)
                        {
                            trace.mark(
                                "macos_initial_space_behavior",
                                json!({"action":"restored","reason":"shell","restored_bits":bits}),
                            );
                        }
                        if let Some(view) = &view {
                            trace.mark("ui_window_state", ui_window_state(&window, view));
                        }
                        last_worker_phase = "python_spawn";
                        let (sender, receiver) = mpsc::sync_channel(32);
                        input = Some(sender);
                        deadline = Some(Instant::now() + Duration::from_secs(10));
                        trace.mark("backend_deadline_started", json!({"timeout_ms":10000}));
                        start_backend(
                            BackendPaths {
                                python: python.clone(),
                                root: config.root.clone(),
                                log: instance.join("worker.log"),
                            },
                            session.clone(),
                            receiver,
                            proxy.clone(),
                            closing.clone(),
                            trace.clone(),
                        );
                    } else if message.get("type") == Some(&json!("hello"))
                        && started
                        && pending_hello.is_none()
                        && !backend_ready
                    {
                        trace.mark("sdk_hello_received", Value::Null);
                        pending_hello = Some(text);
                    } else if backend_ready
                        && matches!(
                            message.get("type").and_then(Value::as_str),
                            Some("hello" | "call" | "cancel")
                        )
                    {
                        if message.get("type") == Some(&json!("hello")) {
                            trace.mark("sdk_hello_received", Value::Null);
                        }
                        send(input.as_ref().ok_or("No worker")?, &session, &text)?;
                    } else if message == json!({"v":1,"type":"pyyaki_connect_timeout"}) {
                        trace.mark("sdk_connect_timeout", Value::Null);
                        return Err("SDK connection timed out (10 seconds)".into());
                    } else {
                        return Err("Invalid native UI message or navigation".into());
                    }
                }
                Event::UserEvent(HostEvent::Spawned(child)) => {
                    if !failed && shutdown_at.is_none() {
                        worker = Some(child);
                    }
                }
                Event::UserEvent(HostEvent::BackendReady) if !failed && shutdown_at.is_none() => {
                    #[cfg(target_os = "macos")]
                    if let Some(view) = &view {
                        trace.mark("ui_window_state", ui_window_state(&window, view));
                    }
                    trace.mark("backend_ready", Value::Null);
                    last_worker_phase = "connection";
                    window.set_title(&config.window.title);
                    backend_ready = true;
                    deadline = None;
                    if let Some(hello) = pending_hello.take() {
                        send(input.as_ref().ok_or("No worker")?, &session, &hello)?;
                    }
                }
                Event::UserEvent(HostEvent::StartupPhase(phase))
                    if !failed && shutdown_at.is_none() =>
                {
                    last_worker_phase = phase;
                }
                Event::UserEvent(HostEvent::Reply(message)) if !failed && shutdown_at.is_none() => {
                    if let Some(view) = &view {
                        post(view, &message.value)?;
                        if message.value.get("type") == Some(&json!("welcome")) {
                            trace.mark("sdk_welcome_posted", Value::Null);
                        }
                    }
                }
                Event::UserEvent(HostEvent::Failed(message))
                    if shutdown_at.is_none() && !failed =>
                {
                    return Err(message.into());
                }
                Event::WindowEvent {
                    event: WindowEvent::CloseRequested,
                    ..
                } => {
                    closing.store(true, Ordering::Release);
                    if let Some(sender) = &input {
                        let _ = sender.try_send(r#"{"v":1,"type":"shutdown"}"#.into());
                    }
                    shutdown_at.get_or_insert(Instant::now() + Duration::from_secs(2));
                    window.set_title("PyYaki — closing…");
                }
                _ => {}
            }
            if !started
                && !failed
                && shutdown_at.is_none()
                && Instant::now() >= next_ui_sample
                && let Some(view) = &view
            {
                trace.mark("ui_window_state", ui_window_state(&window, view));
                next_ui_sample = Instant::now() + Duration::from_secs(1);
            }
            #[cfg(target_os = "macos")]
            if !failed
                && shutdown_at.is_none()
                && let (Some(probe), Some(view)) = (&mut timer_probe, &view)
            {
                for (phase, data) in probe.poll(&window, view, started_at)? {
                    diagnostic(true, started_at, phase, data);
                }
            }
            #[cfg(windows)]
            let interrupted = pyyaki_host::native_cli::INTERRUPTED.load(Ordering::Relaxed);
            #[cfg(target_os = "macos")]
            let interrupted = INTERRUPTED.load(Ordering::Relaxed);
            if interrupted && shutdown_at.is_none() {
                closing.store(true, Ordering::Release);
                if let Some(sender) = &input {
                    let _ = sender.try_send(r#"{"v":1,"type":"shutdown"}"#.into());
                }
                shutdown_at = Some(Instant::now() + Duration::from_secs(2));
            }
            if let Some(until) = shutdown_at {
                #[cfg(target_os = "macos")]
                if let Some(bits) =
                    crate::startup_macos::restore(&window, &mut initial_space_behavior)
                {
                    trace.mark(
                        "macos_initial_space_behavior",
                        json!({"action":"restored","reason":"shutdown","restored_bits":bits}),
                    );
                }
                if worker.as_ref().is_none_or(Worker::exited) || Instant::now() >= until {
                    worker.take();
                    input.take();
                    *flow = ControlFlow::ExitWithCode(exit_code);
                }
            } else if deadline.is_some_and(|until| Instant::now() >= until) {
                trace.mark(
                    "host_initialization_timeout",
                    json!({"last_worker_phase":last_worker_phase}),
                );
                return Err("UI/worker initialization timed out (10 seconds)".into());
            } else if backend_ready && worker.as_ref().is_some_and(Worker::exited) && !failed {
                return Err("Python worker exited unexpectedly".into());
            }
            Ok(())
        })();
        if let Err(error) = result {
            #[cfg(target_os = "macos")]
            if let Some(bits) = crate::startup_macos::restore(&window, &mut initial_space_behavior)
            {
                trace.mark(
                    "macos_initial_space_behavior",
                    json!({"action":"restored","reason":"failure","restored_bits":bits}),
                );
            }
            #[cfg(target_os = "macos")]
            if let Some(probe) = &mut timer_probe {
                probe.cancel(&window);
            }
            failed = true;
            exit_code = 1;
            deadline = None;
            closing.store(true, Ordering::Release);
            worker.take();
            input.take();
            trace.mark(
                "backend_failed",
                json!({"message": error.to_string(),"last_worker_phase":last_worker_phase}),
            );
            if let Some(view) = &view {
                let _ = show_view_error(view, &error.to_string(), last_worker_phase, &instance);
            }
            splash = format!(
                "PyYaki: {error}\nLogs: {}\nClose the window to exit.",
                instance.display()
            );
            trace.line(&splash);
            window.set_title("PyYaki — failed");
            window.request_redraw();
        }
    });
}

#[cfg(target_os = "macos")]
pub fn show_error(message: &str) {
    eprintln!("PyYaki: {message}");
}
#[cfg(target_os = "macos")]
fn paint(window: &Window, message: &str) {
    window.set_title(message);
}
#[cfg(target_os = "macos")]
fn post(view: &WebView, value: &Value) -> Result<(), Box<dyn Error>> {
    let json = serde_json::to_string(&serde_json::to_string(value)?)?;
    view.evaluate_script(&format!(
        "window.dispatchEvent(new MessageEvent('__pyyaki_message__', {{data:JSON.parse({json})}}))"
    ))?;
    Ok(())
}
fn show_view_error(
    view: &WebView,
    message: &str,
    phase: &str,
    instance: &std::path::Path,
) -> Result<(), Box<dyn Error>> {
    let payload = json!({"message":message,"phase":phase,
        "host_log":instance.join("host.log").display().to_string(),
        "worker_log":instance.join("worker.log").display().to_string()});
    view.evaluate_script(&format!("({})({payload});", include_str!("../failure.js")))?;
    Ok(())
}
#[cfg(windows)]
fn platform_builder(builder: WebViewBuilder<'_>) -> WebViewBuilder<'_> {
    builder
        .with_https_scheme(true)
        .with_additional_browser_args("")
        .with_browser_accelerator_keys(false)
        .with_default_context_menus(false)
}
#[cfg(target_os = "macos")]
fn platform_builder(builder: WebViewBuilder<'_>) -> WebViewBuilder<'_> {
    builder
}
fn bootstrap() -> String {
    let mode = if cfg!(target_os = "macos") {
        "macos"
    } else {
        "windows"
    };
    format!(
        "if (window.top === window) Object.defineProperty(window, '__PYYAKI_NATIVE__', {{value:Object.freeze({{mode:'{mode}',version:'{VERSION}'}}),writable:false,configurable:false}});"
    )
}
#[cfg(windows)]
fn instance_base() -> Result<PathBuf, Box<dyn Error>> {
    Ok(std::env::var_os("LOCALAPPDATA")
        .ok_or("LOCALAPPDATA is missing")?
        .into())
}
#[cfg(target_os = "macos")]
fn instance_base() -> Result<PathBuf, Box<dyn Error>> {
    Ok(
        PathBuf::from(std::env::var_os("HOME").ok_or("HOME is missing")?)
            .join("Library/Application Support"),
    )
}

#[cfg(test)]
mod queue_tests {
    use super::*;

    #[test]
    fn queue_capacity_is_released_when_messages_are_dropped() {
        let count = Arc::new(AtomicUsize::new(0));
        let first = QueuedMessage::new("first", &count, 2).unwrap();
        let second = QueuedMessage::new("second", &count, 2).unwrap();
        assert!(QueuedMessage::new("overflow", &count, 2).is_err());
        assert_eq!(count.load(Ordering::Acquire), 2);
        // EventLoopProxy may drop a message on either the producer or GUI thread.
        thread::spawn(move || drop(first)).join().unwrap();
        let replacement = QueuedMessage::new("replacement", &count, 2).unwrap();
        drop(second);
        drop(replacement);
        assert_eq!(count.load(Ordering::Acquire), 0);
    }
}
