//! Native-first, cancellable preparation. The GUI child uses the unchanged run path.
use crate::process::HostChild;
use pyyaki_host::package::Package;
use serde_json::json;
use std::{
    error::Error,
    fs::{self, File},
    io::Write,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};
use tao::{
    dpi::{LogicalSize, PhysicalSize},
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoop},
    platform::windows::{IconExtWindows, WindowBuilderExtWindows},
    window::{Icon, WindowBuilder},
};

pub fn run() -> Result<(), Box<dyn Error>> {
    let started = Instant::now();
    let executable = std::env::current_exe()?;
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() > 1
        || args
            .first()
            .is_some_and(|arg| arg != "--smoke-test" && arg != "--diagnostics")
    {
        return Err("Onefile supports no argument, --smoke-test or --diagnostics".into());
    }
    let base = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is missing")?);
    if !base.is_absolute() {
        return Err("LOCALAPPDATA must be absolute".into());
    }
    let mut random = [0u8; 16];
    getrandom::fill(&mut random).map_err(|_| "OS randomness unavailable")?;
    let session: String = random.iter().map(|b| format!("{b:02x}")).collect();
    let logs = base.join("PyYaki/prototype").join(session);
    fs::create_dir_all(&logs)?;
    let mut log = File::create(logs.join("launcher.log"))?;
    let event_loop = EventLoop::new();
    let window = WindowBuilder::new()
        .with_title("PyYaki — preparing…")
        .with_inner_size(LogicalSize::new(640, 360))
        .with_window_icon(Some(Icon::from_resource(1, None)?))
        .with_taskbar_icon(Some(Icon::from_resource(
            1,
            Some(PhysicalSize::new(256, 256)),
        )?))
        .build(&event_loop)?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let (sender, receiver) = mpsc::channel();
    let mut preparation_started = false;
    let mut child: Option<HostChild> = None;
    let mut message = "PyYaki — verifying and preparing bundled runtime…".to_string();
    let mut failed = false;
    let mut log_phase = move |phase: &str, data: serde_json::Value| {
        let record =
            json!({"phase":phase,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,"data":data});
        let _ = writeln!(log, "pyyaki_onefile={record}");
        let _ = log.flush();
    };
    log_phase(
        "launcher_started",
        json!({"pid":std::process::id(),"executable":executable}),
    );
    window.request_redraw();
    event_loop.run(move |event, _, flow| {
        *flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(50));
        match event {
            Event::RedrawRequested(_) => {
                crate::host::paint(&window, &message);
                if !preparation_started {
                    preparation_started = true;
                    log_phase("launcher_native_draw", json!(null));
                    let sender = sender.clone();
                    let cancelled = cancelled.clone();
                    let base = base.clone();
                    let executable = executable.clone();
                    log_phase("preparation_started", json!(null));
                    std::thread::spawn(move || {
                        let result = (|| {
                            let mut package = Package::open(&executable)?;
                            package.prepare(&base, &cancelled)
                        })()
                        .map_err(|error: std::io::Error| error.to_string());
                        let _ = sender.send(result);
                    });
                }
            }
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                cancelled.store(true, Ordering::Release);
                log_phase("launcher_close", json!(null));
                child.take(); // Only our Job; closes its child host and nested worker tree.
                *flow = ControlFlow::ExitWithCode(i32::from(failed));
                return;
            }
            _ => {}
        }
        let result = (|| -> Result<(), Box<dyn Error>> {
            if let Ok(result) = receiver.try_recv() {
                let (cache, reused) = result.map_err(std::io::Error::other)?;
                log_phase(
                    "preparation_completed",
                    json!({"cache":cache,"reused":reused}),
                );
                let owned =
                    HostChild::spawn(&executable, &cache, args.first().map(|arg| arg.as_os_str()))?;
                log_phase("gui_child_started", json!({"pid":owned.pid}));
                child = Some(owned);
                window.set_visible(false);
            }
            if let Some(owned) = &child
                && let Some(code) = owned.exit_code()?
            {
                log_phase("gui_child_exited", json!({"code":code}));
                child.take();
                *flow = ControlFlow::ExitWithCode(code as i32);
            }
            Ok(())
        })();
        if let Err(error) = result {
            cancelled.store(true, Ordering::Release);
            child.take();
            failed = true;
            message = format!(
                "PyYaki: {error}\nLogs: {}\nClose the window to exit.",
                logs.display()
            );
            log_phase("preparation_failed", json!({"error":error.to_string()}));
            window.set_title("PyYaki — preparation failed");
            window.set_visible(true);
            window.request_redraw();
        }
    });
}
