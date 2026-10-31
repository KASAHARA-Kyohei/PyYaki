use crate::ORIGIN;
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Default)]
pub struct NavigationGate(AtomicBool);

impl NavigationGate {
    pub fn allow(&self, url: &str) -> bool {
        url == format!("{ORIGIN}/") && !self.0.swap(true, Ordering::AcqRel)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn allows_only_the_initial_top_page() {
        let gate = NavigationGate::default();
        assert!(!gate.allow("https://evil.example/"));
        assert!(!gate.allow("https://pyyaki.localhost.evil.example/"));
        assert!(gate.allow(&format!("{ORIGIN}/")));
        assert!(!gate.allow(&format!("{ORIGIN}/")));
        assert!(!gate.allow("https://pyyaki.localhost/other.html"));
    }
}
