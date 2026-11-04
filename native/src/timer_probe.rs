//! Explicit maintainer-only window cycle. Rust, not throttled JS, restores the window.
use serde_json::{Value, json};
use std::{
    error::Error,
    time::{Duration, Instant},
};
use tao::window::Window;
use wry::WebView;

const PHASES: [&str; 7] = [
    "foreground",
    "background",
    "covered",
    "minimized",
    "restored",
    "hidden",
    "recovered",
];

pub struct TimerProbe {
    control: Window,
    duration: Duration,
    phase: Option<usize>,
    phase_started: Instant,
    previous_tick: Instant,
    samples: Vec<Value>,
    finished: bool,
}

impl TimerProbe {
    pub fn new(control: Window, seconds: u64) -> Self {
        Self {
            control,
            duration: Duration::from_secs(seconds),
            phase: None,
            phase_started: Instant::now(),
            previous_tick: Instant::now(),
            samples: Vec::new(),
            finished: false,
        }
    }

    pub fn start(
        &mut self,
        window: &Window,
        view: &WebView,
        origin: Instant,
    ) -> Result<(&'static str, Value), Box<dyn Error>> {
        if self.phase.is_some() || self.finished {
            return Err("Timer probe already started".into());
        }
        self.enter(0, window, view, origin)
    }

    fn enter(
        &mut self,
        index: usize,
        window: &Window,
        view: &WebView,
        origin: Instant,
    ) -> Result<(&'static str, Value), Box<dyn Error>> {
        match PHASES.get(index).copied() {
            Some("background") => {
                window.set_visible(true);
                window.set_minimized(false);
                self.control
                    .set_inner_size(tao::dpi::LogicalSize::new(300, 160));
                self.control.set_visible(true);
                self.control.set_focus();
            }
            Some("covered") => {
                window.set_visible(true);
                window.set_minimized(false);
                let position = window.outer_position()?;
                let size = window.outer_size();
                self.control
                    .set_outer_position(tao::dpi::PhysicalPosition::new(
                        position.x - 30,
                        position.y - 30,
                    ));
                self.control.set_inner_size(tao::dpi::PhysicalSize::new(
                    size.width + 60,
                    size.height + 60,
                ));
                self.control.set_visible(true);
                self.control.set_focus();
            }
            Some("minimized") => {
                window.set_minimized(true);
                self.control
                    .set_inner_size(tao::dpi::LogicalSize::new(300, 160));
                self.control.set_focus();
            }
            Some("hidden") => {
                window.set_minimized(false);
                window.set_visible(false);
                self.control.set_visible(true);
                self.control.set_focus();
            }
            _ => {
                window.set_visible(true);
                window.set_minimized(false);
                window.set_focus();
                self.control.set_visible(false);
            }
        }
        let now = Instant::now();
        self.phase_started = now;
        self.previous_tick = now;
        self.phase = PHASES.get(index).map(|_| index);
        self.finished = self.phase.is_none();
        let data = json!({"index":index,"name":PHASES.get(index).unwrap_or(&"done"),
            "native_start_ms":origin.elapsed().as_secs_f64()*1000.0});
        view.evaluate_script(&format!(
            "window.dispatchEvent(new CustomEvent('__pyyaki_timer_phase__',{{detail:{data}}}));"
        ))?;
        Ok(("timer_phase", data))
    }

    pub fn poll(
        &mut self,
        window: &Window,
        view: &WebView,
        origin: Instant,
    ) -> Result<Vec<(&'static str, Value)>, Box<dyn Error>> {
        let Some(index) = self.phase else {
            return Ok(Vec::new());
        };
        let now = Instant::now();
        let delta = now.duration_since(self.previous_tick);
        if delta >= Duration::from_millis(100) {
            self.samples
                .push(json!({"lag_ms":(delta.as_secs_f64()*1000.0-100.0).max(0.0),
                "elapsed_ms":now.duration_since(self.phase_started).as_secs_f64()*1000.0,
                "focused":window.is_focused(),"visible":window.is_visible(),
                "minimized":window.is_minimized()}));
            self.previous_tick = now;
        }
        if now.duration_since(self.phase_started) < self.duration {
            return Ok(Vec::new());
        }
        let segment = json!({"index":index,"name":PHASES[index],
            "duration_ms":now.duration_since(self.phase_started).as_secs_f64()*1000.0,
            "samples":std::mem::take(&mut self.samples)});
        let next = self.enter(index + 1, window, view, origin)?;
        Ok(vec![("timer_native_segment", segment), next])
    }

    pub fn cancel(&mut self, window: &Window) {
        self.phase = None;
        self.finished = true;
        window.set_visible(true);
        window.set_minimized(false);
        window.set_focus();
        self.control.set_visible(false);
    }
}
