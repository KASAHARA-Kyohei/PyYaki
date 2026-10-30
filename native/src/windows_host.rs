use crate::process::Worker;
use pyyaki_host::navigation::NavigationGate;
use pyyaki_host::{MAX_MESSAGE_BYTES, ORIGIN, VERSION, assets, config::Config, framing};
use serde_json::{Value, json};
use std::{
    error::Error,
    fs,
    io::Write,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc::{self, SyncSender},
    },
    thread,
    time::{Duration, Instant},
};
use tao::{
    dpi::LogicalSize,
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy},
    platform::windows::WindowExtWindows,
    window::{Window, WindowBuilder},
};
use windows::core::PCWSTR;
use windows_sys::Win32::{
    Foundation::RECT,
    Graphics::Gdi::{
        CreateSolidBrush, DT_CENTER, DT_NOPREFIX, DT_WORDBREAK, DeleteObject, DrawTextW, FillRect,
        GetDC, ReleaseDC, SetBkMode, SetTextColor, TRANSPARENT,
    },
    UI::WindowsAndMessaging::{GetClientRect, MB_ICONERROR, MB_OK, MessageBoxW},
};
use wry::{
    WebContext, WebView, WebViewBuilder, WebViewBuilderExtWindows, WebViewExtWindows,
    http::Response,
};

enum HostEvent {
    Ipc(IpcMessage),
    Spawned(Worker),
    BackendReady,
    Reply(Value),
    Failed(String),
}

struct IpcMessage {
    text: String,
    count: Arc<AtomicUsize>,
}
impl Drop for IpcMessage {
    fn drop(&mut self) {
        self.count.fetch_sub(1, Ordering::AcqRel);
    }
}

fn token() -> Result<String, Box<dyn Error>> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("OS randomness failed: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain([0]).collect()
}

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

fn paint(window: &Window, message: &str) {
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

fn post(view: &WebView, value: &Value) -> Result<(), Box<dyn Error>> {
    let text = wide(&serde_json::to_string(value)?);
    // SAFETY: WebView2 is used on its creating STA thread and copies the valid JSON string.
    unsafe {
        view.webview().PostWebMessageAsJson(PCWSTR(text.as_ptr()))?;
    }
    Ok(())
}

fn start_backend(
    python: PathBuf,
    root: PathBuf,
    log: PathBuf,
    session: String,
    receiver: mpsc::Receiver<String>,
    proxy: EventLoopProxy<HostEvent>,
    closing: Arc<AtomicBool>,
) {
    thread::spawn(move || {
        let result = (|| -> Result<(), Box<dyn Error>> {
            let worker = Worker::spawn(&python, &root, &log)?;
            if closing.load(Ordering::Acquire) {
                return Ok(());
            }
            let mut reader = worker.read.try_clone()?;
            let mut writer = worker.write.try_clone()?;
            proxy
                .send_event(HostEvent::Spawned(worker))
                .map_err(|_| "GUI closed")?;
            let nonce = token()?;
            framing::write(
                &mut writer,
                &json!({"v":1,"type":"worker_hello", "nonce":nonce,
                "framework_version":VERSION}),
            )?;
            let welcome = framing::read(&mut reader)?;
            if welcome
                != json!({"v":1,"type":"worker_welcome", "nonce":nonce,"framework_version":VERSION})
            {
                return Err("Worker handshake or framework version mismatch".into());
            }
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
            loop {
                let frame = framing::read(&mut reader)?;
                if frame.get("v") != Some(&json!(1))
                    || frame.get("type") != Some(&json!("dispatch"))
                    || frame.get("session_id") != Some(&json!(session))
                    || frame.as_object().is_none_or(|o| o.len() != 4)
                {
                    return Err("Invalid worker dispatch frame".into());
                }
                proxy
                    .send_event(HostEvent::Reply(frame["message"].clone()))
                    .map_err(|_| "GUI closed")?;
            }
        })();
        if let Err(error) = result
            && !closing.load(Ordering::Acquire)
        {
            let _ = proxy.send_event(HostEvent::Failed(format!(
                "{error}. Worker log: {}",
                log.display()
            )));
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

pub fn run() -> Result<(), Box<dyn Error>> {
    let started_at = Instant::now();
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(std::ffi::OsStr::new("run")) {
        return Err("Usage: pyyaki.exe run --project <project> --python <venv-python.exe>".into());
    }
    let mut root = std::env::current_dir()?;
    let mut python = None;
    while let Some(arg) = args.next() {
        match arg.to_str() {
            Some("--project") => root = PathBuf::from(args.next().ok_or("Missing project path")?),
            Some("--python") => {
                python =
                    Some(PathBuf::from(args.next().ok_or("Missing Python path")?).canonicalize()?)
            }
            _ => return Err("Unknown native argument".into()),
        }
    }
    let python = python.ok_or("Specify --python with the installed PyYaki development venv")?;
    let config = Arc::new(Config::load(&root)?);
    let session = token()?;
    let instance = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is missing")?;
    let instance = PathBuf::from(instance)
        .join("PyYaki")
        .join("prototype")
        .join(&session);
    fs::create_dir_all(&instance)?;
    let mut host_log = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(instance.join("host.log"))?;
    let event_loop = EventLoopBuilder::<HostEvent>::with_user_event().build();
    let window = WindowBuilder::new()
        .with_title(&config.window.title)
        .with_inner_size(LogicalSize::new(config.window.width, config.window.height))
        .build(&event_loop)?;
    let proxy = event_loop.create_proxy();
    let closing = Arc::new(AtomicBool::new(false));
    let mut context = WebContext::new(Some(instance.join("profile")));
    let mut view: Option<WebView> = None;
    let mut worker: Option<Worker> = None;
    let mut input: Option<SyncSender<String>> = None;
    let mut pending_hello: Option<String> = None;
    let mut started = false;
    let mut backend_ready = false;
    let mut failed = false;
    let mut deadline: Option<Instant> = None;
    let mut shutdown_at: Option<Instant> = None;
    let mut splash = "PyYaki — preparing native UI…".to_string();
    window.request_redraw();
    event_loop.run(move |event, _, flow| {
        *flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100));
        let result = (|| -> Result<(), Box<dyn Error>> {
            match event {
                Event::RedrawRequested(_) if view.is_none() => {
                    paint(&window, &splash);
                    if failed { return Ok(()); }
                    let _ = writeln!(host_log, "host_elapsed/native_draw={:?}", started_at.elapsed());
                    let protocol_config = config.clone();
                    let navigation = NavigationGate::default();
                    let ipc_proxy = proxy.clone();
                    let ipc_count = Arc::new(AtomicUsize::new(0));
                    let ipc_overflow = AtomicBool::new(false);
                    let active_assets = Arc::new(AtomicUsize::new(0));
                    let new_view = WebViewBuilder::new_with_web_context(&mut context)
                        .with_https_scheme(true)
                        .with_additional_browser_args("") // Keep SmartScreen protection enabled.
                        .with_devtools(false).with_browser_accelerator_keys(false)
                        .with_default_context_menus(false)
                        .with_download_started_handler(|_, _| false)
                        .with_navigation_handler(move |url| navigation.allow(&url))
                        .with_new_window_req_handler(|_, _| wry::NewWindowResponse::Deny)
                        .with_initialization_script("if (window.top === window) Object.defineProperty(window, '__PYYAKI_NATIVE__', {value: Object.freeze({mode:'windows', version:'0.1.0.dev0'}), writable:false, configurable:false});")
                        .with_ipc_handler(move |request| {
                            if request.uri().to_string() == format!("{ORIGIN}/") && request.body().len() <= MAX_MESSAGE_BYTES {
                                if ipc_count.fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| (count < 32).then_some(count + 1)).is_ok() {
                                    let message = IpcMessage { text: request.body().clone(), count: ipc_count.clone() };
                                    let _ = ipc_proxy.send_event(HostEvent::Ipc(message));
                                } else if !ipc_overflow.swap(true, Ordering::AcqRel) {
                                    let _ = ipc_proxy.send_event(HostEvent::Failed("Native IPC queue limit exceeded".into()));
                                }
                            }
                        })
                        .with_asynchronous_custom_protocol("pyyaki".into(), move |_, request, responder| {
                            if active_assets.fetch_add(1, Ordering::AcqRel) >= 16 {
                                active_assets.fetch_sub(1, Ordering::AcqRel);
                                responder.respond(Response::builder().status(503).body(Vec::new()).unwrap());
                                return;
                            }
                            let active = active_assets.clone();
                            let config = protocol_config.clone();
                            thread::spawn(move || {
                                let response = if request.method() == "GET" && request.uri().scheme_str() == Some("pyyaki") && request.uri().host() == Some("localhost") {
                                    assets::resolve(&config.ui_dir, &config.ui_entry, request.uri().path())
                                } else { Err(std::io::Error::other("Rejected asset request")) };
                                let (status, body, mime) = match response {
                                    Ok((body, mime)) => (200, body, mime),
                                    Err(_) => (404, Vec::new(), "text/plain"),
                                };
                                responder.respond(Response::builder().status(status)
                                    .header("Content-Type", mime).header("Content-Security-Policy", assets::CSP)
                                    .header("X-Content-Type-Options", "nosniff").header("Cache-Control", "no-store")
                                    .body(body).unwrap());
                                active.fetch_sub(1, Ordering::AcqRel);
                            });
                        })
                        .with_url("pyyaki://localhost/").build(&window)?;
                    view = Some(new_view);
                    deadline = Some(Instant::now() + Duration::from_secs(10));
                }
                Event::UserEvent(HostEvent::Ipc(mut incoming)) if !failed && shutdown_at.is_none() => {
                    let text = std::mem::take(&mut incoming.text);
                    drop(incoming);
                    let message: Value = serde_json::from_str(&text)?;
                    if message == json!({"v":1,"type":"pyyaki_shell_ready"}) && !started {
                        started = true;
                        let _ = writeln!(host_log, "host_elapsed/shell_marker={:?}", started_at.elapsed());
                        let (sender, receiver) = mpsc::sync_channel(32);
                        input = Some(sender);
                        deadline = Some(Instant::now() + Duration::from_secs(10));
                        start_backend(python.clone(), config.root.clone(), instance.join("worker.log"),
                            session.clone(), receiver, proxy.clone(), closing.clone());
                    } else if message.get("type") == Some(&json!("hello")) && started && pending_hello.is_none() && !backend_ready {
                        pending_hello = Some(text);
                    } else if backend_ready && matches!(message.get("type").and_then(Value::as_str), Some("hello" | "call" | "cancel")) {
                        send(input.as_ref().ok_or("No worker")?, &session, &text)?;
                    } else { return Err("Invalid native UI message or navigation".into()); }
                }
                Event::UserEvent(HostEvent::Spawned(child)) => {
                    if !failed && shutdown_at.is_none() { worker = Some(child); }
                }
                Event::UserEvent(HostEvent::BackendReady) if !failed && shutdown_at.is_none() => {
                    backend_ready = true;
                    deadline = None;
                    if let Some(hello) = pending_hello.take() { send(input.as_ref().ok_or("No worker")?, &session, &hello)?; }
                }
                Event::UserEvent(HostEvent::Reply(message)) if !failed && shutdown_at.is_none() => {
                    if let Some(view) = &view { post(view, &message)?; }
                }
                Event::UserEvent(HostEvent::Failed(message)) if shutdown_at.is_none() && !failed => return Err(message.into()),
                Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => {
                    closing.store(true, Ordering::Release);
                    if let Some(sender) = &input { let _ = sender.try_send(r#"{"v":1,"type":"shutdown"}"#.into()); }
                    shutdown_at.get_or_insert(Instant::now() + Duration::from_secs(2));
                    window.set_title("PyYaki — closing…");
                }
                _ => {}
            }
            if let Some(until) = shutdown_at {
                if worker.as_ref().is_none_or(Worker::exited) || Instant::now() >= until {
                    worker.take(); input.take(); *flow = ControlFlow::Exit;
                }
            } else if deadline.is_some_and(|until| Instant::now() >= until) {
                return Err("UI/worker initialization timed out (10 seconds)".into());
            } else if backend_ready && worker.as_ref().is_some_and(Worker::exited) && !failed {
                return Err("Python worker exited unexpectedly".into());
            }
            Ok(())
        })();
        if let Err(error) = result {
            failed = true; deadline = None; closing.store(true, Ordering::Release);
            worker.take(); input.take();
            if let Some(view) = view.take() { let _ = view.set_visible(false); }
            splash = format!("PyYaki: {error} — close the window to exit.");
            let _ = writeln!(host_log, "{splash}");
            window.set_title("PyYaki — failed");
            window.request_redraw();
        }
    });
}
