#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(target_os = "macos")]
mod bundle;
#[cfg(any(windows, target_os = "macos"))]
mod host;
#[cfg(target_os = "macos")]
mod icon_macos;
#[cfg(windows)]
mod onefile;
#[cfg(windows)]
mod process;
#[cfg(target_os = "macos")]
#[path = "process_macos.rs"]
mod process;
#[cfg(target_os = "macos")]
mod timer_probe;

#[cfg(any(windows, target_os = "macos"))]
fn main() {
    #[cfg(windows)]
    if std::env::args_os()
        .nth(1)
        .is_none_or(|arg| arg == "--smoke-test" || arg == "--diagnostics")
    {
        if let Err(error) = onefile::run() {
            host::show_error(&error.to_string());
            std::process::exit(1);
        }
        return;
    }
    if let Err(error) = host::run() {
        host::show_error(&error.to_string());
        std::process::exit(1);
    }
}

#[cfg(not(any(windows, target_os = "macos")))]
fn main() {
    eprintln!("PyYaki native GUI supports Windows and macOS; use `pyyaki dev` on this platform.");
    std::process::exit(1);
}
