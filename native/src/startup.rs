//! Private worker startup messages. A fixed sequence bounds work and GUI notices.
use crate::VERSION;
use serde::Deserialize;
use serde_json::Value;

pub const PHASES: [&str; 6] = [
    "worker_entered",
    "config_loaded",
    "app_import_started",
    "app_imported",
    "startup_hooks_started",
    "startup_hooks_completed",
];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Notice {
    v: u8,
    #[serde(rename = "type")]
    kind: String,
    nonce: String,
    framework_version: String,
    phase: String,
    worker_elapsed_ms: f64,
    #[serde(default)]
    error_type: Option<String>,
    #[serde(default)]
    message: Option<String>,
}

pub enum Update {
    Progress {
        phase: &'static str,
        elapsed_ms: f64,
    },
    Ready,
    Failed(String),
}

#[derive(Default)]
pub struct Startup {
    next: usize,
    elapsed_ms: f64,
    ready: bool,
}

impl Startup {
    pub fn accept(&mut self, value: Value, nonce: &str) -> Result<Update, &'static str> {
        if !self.ready
            && self.next == PHASES.len()
            && value
                == serde_json::json!({"v":1,"type":"worker_welcome",
                "nonce":nonce,"framework_version":VERSION})
        {
            self.ready = true;
            return Ok(Update::Ready);
        }
        let field_count = value
            .as_object()
            .ok_or("Invalid worker startup frame")?
            .len();
        let notice: Notice =
            serde_json::from_value(value).map_err(|_| "Invalid worker startup frame")?;
        if notice.v != 1
            || notice.nonce != nonce
            || notice.framework_version != VERSION
            || !notice.worker_elapsed_ms.is_finite()
            || notice.worker_elapsed_ms < self.elapsed_ms
        {
            return Err("Invalid worker startup identity or clock");
        }
        if notice.kind == "worker_progress"
            && !self.ready
            && field_count == 6
            && PHASES.get(self.next) == Some(&notice.phase.as_str())
        {
            let phase = PHASES[self.next];
            self.next += 1;
            self.elapsed_ms = notice.worker_elapsed_ms;
            return Ok(Update::Progress {
                phase,
                elapsed_ms: self.elapsed_ms,
            });
        }
        if notice.kind == "worker_failed" && field_count == 8 {
            let valid_phase = if self.ready {
                notice.phase == "connection"
            } else {
                self.next > 0 && PHASES[self.next - 1] == notice.phase
            };
            if let (true, Some(error_type), Some(message)) =
                (valid_phase, notice.error_type, notice.message)
                && !error_type.is_empty()
                && error_type.chars().count() <= 128
                && message.chars().count() <= 1024
            {
                return Ok(Update::Failed(format!(
                    "Python failed during {}: {error_type}: {message}",
                    notice.phase
                )));
            }
        }
        Err("Invalid worker startup phase or message")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn progress(phase: &str, ms: f64) -> Value {
        json!({"v":1,"type":"worker_progress","nonce":"test",
            "framework_version":VERSION,"phase":phase,"worker_elapsed_ms":ms})
    }

    #[test]
    fn accepts_only_ordered_bounded_startup_then_welcome() {
        let welcome = json!({"v":1,"type":"worker_welcome",
            "nonce":"test","framework_version":VERSION});
        let mut startup = Startup::default();
        assert!(startup.accept(welcome.clone(), "test").is_err());
        for (index, phase) in PHASES.iter().enumerate() {
            assert!(
                matches!(startup.accept(progress(phase, index as f64), "test"),
                Ok(Update::Progress { phase: accepted, .. }) if accepted == *phase)
            );
            assert!(
                startup
                    .accept(progress(phase, index as f64), "test")
                    .is_err()
            );
        }
        assert!(matches!(
            startup.accept(welcome.clone(), "test"),
            Ok(Update::Ready)
        ));
        assert!(startup.accept(welcome, "test").is_err());
        assert!(startup.accept(progress(PHASES[0], 8.0), "test").is_err());
    }

    #[test]
    fn rejects_forged_clock_identity_and_extra_fields() {
        for field in [
            "v",
            "nonce",
            "framework_version",
            "phase",
            "worker_elapsed_ms",
            "extra",
        ] {
            let mut value = progress(PHASES[0], 1.0);
            value[field] = json!("invalid");
            assert!(Startup::default().accept(value, "test").is_err(), "{field}");
        }
        let mut startup = Startup::default();
        startup.accept(progress(PHASES[0], 5.0), "test").unwrap();
        assert!(startup.accept(progress(PHASES[1], 4.0), "test").is_err());
    }

    #[test]
    fn failure_is_bounded_and_tied_to_observed_phase() {
        let mut startup = Startup::default();
        startup.accept(progress(PHASES[0], 0.0), "test").unwrap();
        let mut failure = progress(PHASES[0], 1.0);
        failure["type"] = json!("worker_failed");
        failure["error_type"] = json!("RuntimeError");
        failure["message"] = json!("原因 <script> 😀");
        assert!(
            matches!(startup.accept(failure.clone(), "test"), Ok(Update::Failed(text))
            if text.contains("RuntimeError: 原因 <script> 😀"))
        );
        failure["message"] = json!("😀".repeat(1025));
        assert!(startup.accept(failure, "test").is_err());
    }
}
