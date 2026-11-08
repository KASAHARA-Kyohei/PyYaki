//! Temporary initial Space placement; normal window behavior resumes at shell/failure/close.
use objc2::MainThreadMarker;
use objc2_app_kit::{NSWindow, NSWindowCollectionBehavior};
use tao::{platform::macos::WindowExtMacOS, window::Window};

fn native_window(window: &Window) -> &NSWindow {
    MainThreadMarker::new().expect("Initial Space placement requires the GUI thread");
    let pointer = window.ns_window().cast::<NSWindow>();
    assert!(!pointer.is_null(), "Tao must provide a live NSWindow");
    // SAFETY: Tao returns its live NSWindow (or subclass), aligned and initialized.
    // The borrow is tied to the owning Window; no raw pointer or borrow is stored.
    // The main-thread check above precedes all AppKit access.
    unsafe { &*pointer }
}

fn initial_behavior(original: NSWindowCollectionBehavior) -> NSWindowCollectionBehavior {
    // AppKit forbids combining CanJoinAllSpaces and MoveToActiveSpace.
    // Preserve the unrelated fullscreen/tiling/cycle policy.
    (original & !NSWindowCollectionBehavior::CanJoinAllSpaces)
        | NSWindowCollectionBehavior::MoveToActiveSpace
}

pub fn prepare(window: &Window) -> (NSWindowCollectionBehavior, NSWindowCollectionBehavior) {
    let native = native_window(window);
    let original = native.collectionBehavior();
    let applied = initial_behavior(original);
    native.setCollectionBehavior(applied);
    (original, applied)
}

pub fn restore(
    window: &Window,
    original: &mut Option<NSWindowCollectionBehavior>,
) -> Option<usize> {
    let original = original.take()?;
    native_window(window).setCollectionBehavior(original);
    Some(original.bits())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn initial_space_policy_is_exclusive_and_preserves_other_window_behavior() {
        let normal = NSWindowCollectionBehavior::FullScreenPrimary
            | NSWindowCollectionBehavior::Managed
            | NSWindowCollectionBehavior::ParticipatesInCycle;
        for original in [
            normal,
            normal | NSWindowCollectionBehavior::CanJoinAllSpaces,
        ] {
            let applied = initial_behavior(original);
            assert!(applied.contains(NSWindowCollectionBehavior::MoveToActiveSpace));
            assert!(!applied.contains(NSWindowCollectionBehavior::CanJoinAllSpaces));
            assert!(applied.contains(normal));
        }
    }
}
