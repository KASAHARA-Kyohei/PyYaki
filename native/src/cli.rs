//! Windows wheel entry point. No Python is loaded before entering the GUI host.
#[cfg(windows)]
#[allow(dead_code)]
mod host;
#[cfg(windows)]
#[allow(dead_code)] // Onefile HostChild is shared with the standalone GUI binary.
mod process;

#[cfg(windows)]
fn main() {
    use pyyaki_host::native_cli::{Action, dispatch};
    let result = dispatch(std::env::args_os().skip(1).collect()).and_then(|action| match action {
        Action::Done(code) => Ok(code),
        Action::Run(args) => {
            pyyaki_host::native_cli::install_console_handler(false)?;
            host::run_with_args(args).map(|()| 0)
        }
    });
    match result {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("pyyaki: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(not(windows))]
fn main() {
    eprintln!("pyyaki-cli is a Windows wheel entry point");
    std::process::exit(1);
}
