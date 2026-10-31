#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(any(windows, target_os = "macos"))]
mod host;
#[cfg(windows)]
mod process;
#[cfg(target_os = "macos")]
#[path = "process_macos.rs"]
mod process;

#[cfg(any(windows, target_os = "macos"))]
fn main() {
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
