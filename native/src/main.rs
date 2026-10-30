#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(windows)]
mod process;
#[cfg(windows)]
mod windows_host;

#[cfg(windows)]
fn main() {
    if let Err(error) = windows_host::run() {
        windows_host::show_error(&error.to_string());
        std::process::exit(1);
    }
}

#[cfg(not(windows))]
fn main() {
    eprintln!("PyYaki native GUI currently targets Windows only; use `pyyaki dev` on macOS.");
    std::process::exit(1);
}
