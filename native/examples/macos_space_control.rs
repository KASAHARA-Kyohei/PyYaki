//! Maintainer fixture: owns only its fullscreen window, never the application under test.
#[cfg(target_os = "macos")]
mod macos {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSApplication, NSWindow, NSWindowOcclusionState, NSWindowStyleMask};
    use serde_json::{Value, json};
    use std::{
        error::Error,
        fs,
        path::Path,
        time::{Duration, Instant},
    };
    use tao::{
        event::{Event, StartCause, WindowEvent},
        event_loop::{ControlFlow, EventLoop},
        platform::macos::WindowExtMacOS,
        window::{Fullscreen, Window, WindowBuilder},
    };

    fn state(window: &Window, started: Instant) -> Value {
        let mtm = MainThreadMarker::new().expect("Space fixture requires the GUI thread");
        let pointer = window.ns_window().cast::<NSWindow>();
        assert!(!pointer.is_null());
        // SAFETY: Tao owns this initialized NSWindow/subclass while window is borrowed.
        // Access is on the GUI thread and no raw pointer or native borrow escapes.
        let native = unsafe { &*pointer };
        json!({"elapsed_ms":started.elapsed().as_millis(),
            "fullscreen":native.styleMask().contains(NSWindowStyleMask::FullScreen),
            "active_space":native.isOnActiveSpace(),
            "unoccluded":native.occlusionState().contains(NSWindowOcclusionState::Visible),
            "application_active":NSApplication::sharedApplication(mtm).isActive()})
    }

    fn write(path: &Path, value: &Value) -> Result<(), Box<dyn Error>> {
        use std::io::Write;
        let mut file = fs::File::create_new(path)?;
        file.write_all(&serde_json::to_vec_pretty(value)?)?;
        Ok(())
    }

    pub fn run() -> Result<(), Box<dyn Error>> {
        let mut args = std::env::args_os().skip(1);
        let output = std::path::PathBuf::from(args.next().ok_or("Specify a new output directory")?);
        if args.next().is_some() {
            return Err("Expected only an output directory".into());
        }
        fs::create_dir(&output)?;
        let pid = std::process::id();
        let event_loop = EventLoop::new();
        let window = WindowBuilder::new()
            .with_title("PyYaki fullscreen regression control")
            .build(&event_loop)?;
        let started = Instant::now();
        let mut stable_since = None;
        let mut ready = false;
        let mut ended = None;
        let mut status = "running";
        let mut next_sample = started;
        let mut samples = Vec::new();
        let mut outcome = None;
        event_loop.run(move |event, _, flow| {
            if matches!(event, Event::LoopDestroyed) {
                return;
            }
            if let Some(code) = outcome {
                *flow = ControlFlow::ExitWithCode(code);
                return;
            }
            *flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(100));
            if matches!(event, Event::NewEvents(StartCause::Init)) {
                window.set_fullscreen(Some(Fullscreen::Borderless(None)));
            }
            if matches!(
                event,
                Event::WindowEvent {
                    event: WindowEvent::CloseRequested,
                    ..
                }
            ) && ended.is_none()
            {
                status = "cancelled";
                ended = Some(Instant::now());
                window.set_fullscreen(None);
            }
            if Instant::now() < next_sample {
                return;
            }
            next_sample = Instant::now() + Duration::from_millis(100);
            let snapshot = state(&window, started);
            let result = (|| -> Result<(), Box<dyn Error>> {
                if ended.is_none() && !ready {
                    if samples.len() < 256 {
                        samples.push(snapshot.clone());
                    }
                    let good = [
                        "fullscreen",
                        "active_space",
                        "unoccluded",
                        "application_active",
                    ]
                    .iter()
                    .all(|key| snapshot[*key] == true);
                    if good {
                        stable_since.get_or_insert(Instant::now());
                    } else {
                        stable_since = None;
                    }
                    if let Some(stable) = stable_since
                        && stable.elapsed() >= Duration::from_secs(2)
                    {
                        write(
                            &output.join("ready.json"),
                            &json!({"pid":pid,
                            "stable_ms":stable.elapsed().as_millis(),"state":snapshot,
                            "samples":samples}),
                        )?;
                        ready = true;
                    }
                }
                if ended.is_none() {
                    let finish = output.join("finish.json");
                    if ready && finish.is_file() {
                        let request: Value = serde_json::from_slice(&fs::read(finish)?)?;
                        if request["controller_pid"] != pid {
                            return Err("Wrong control PID".into());
                        }
                        status = "completed";
                    } else if !ready && started.elapsed() >= Duration::from_secs(20) {
                        status = "not_ready";
                    } else if started.elapsed() >= Duration::from_secs(120) {
                        status = "watchdog";
                    }
                    if status != "running" {
                        ended = Some(Instant::now());
                        window.set_fullscreen(None);
                    }
                }
                if let Some(ended) = ended
                    && (snapshot["fullscreen"] == false
                        || ended.elapsed() >= Duration::from_secs(5))
                {
                    let ok = status == "completed" && snapshot["fullscreen"] == false;
                    write(
                        &output.join("report.json"),
                        &json!({"pid":pid,"status":status,
                        "ok":ok,"ready":ready,"state":snapshot}),
                    )?;
                    let code = if ok { 0 } else { 2 };
                    outcome = Some(code);
                    *flow = ControlFlow::ExitWithCode(code);
                }
                Ok(())
            })();
            if let Err(error) = result {
                eprintln!("Space fixture: {error}");
                outcome = Some(1);
                *flow = ControlFlow::ExitWithCode(1);
            }
        });
    }
}

#[cfg(target_os = "macos")]
fn main() {
    if let Err(error) = macos::run() {
        eprintln!("Space fixture: {error}");
        std::process::exit(1);
    }
}

#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("The Space control fixture requires macOS");
    std::process::exit(2);
}
