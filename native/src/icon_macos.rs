use objc2::{AnyThread, MainThreadMarker};
use objc2_app_kit::{NSApplication, NSImage};
use objc2_foundation::NSData;
use std::error::Error;

/// Called on the GUI thread after Tao initializes NSApplication.
pub fn set_development_icon() -> Result<(), Box<dyn Error>> {
    let mtm = MainThreadMarker::new().ok_or("Application icon requires the main thread")?;
    let data = NSData::with_bytes(include_bytes!("../icons/pyyaki-dock.png"));
    let image =
        NSImage::initWithData(NSImage::alloc(), &data).ok_or("Invalid embedded app icon")?;
    let application = NSApplication::sharedApplication(mtm);
    // SAFETY: on the main thread; Some references a valid retained NSImage, never nil.
    // AppKit retains the application icon, so it outlives these local references.
    unsafe { application.setApplicationIconImage(Some(&image)) };
    Ok(())
}
