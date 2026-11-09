//! Resolve only the venv containing this executable; never probe Python or search PATH.
use crate::VERSION;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::sync::atomic::{AtomicBool, Ordering};
use std::{
    error::Error,
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    process::Command,
};
use windows_sys::Win32::System::Console::{CTRL_BREAK_EVENT, CTRL_C_EVENT, SetConsoleCtrlHandler};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
    SetInformationJobObject,
};
use windows_sys::Win32::System::Threading::GetCurrentProcess;

pub static INTERRUPTED: AtomicBool = AtomicBool::new(false);
static DELEGATED: AtomicBool = AtomicBool::new(false);

unsafe extern "system" fn console_handler(event: u32) -> i32 {
    if event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT {
        if !DELEGATED.load(Ordering::Relaxed) {
            INTERRUPTED.store(true, Ordering::Relaxed);
        }
        1
    } else {
        0
    }
}

pub fn install_console_handler(delegated: bool) -> Result<(), Box<dyn Error>> {
    DELEGATED.store(delegated, Ordering::Relaxed);
    // SAFETY: callback has static lifetime and only accesses lock-free atomics.
    if unsafe { SetConsoleCtrlHandler(Some(console_handler), 1) } == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(())
}

fn own_delegation_job() -> Result<(), Box<dyn Error>> {
    // SAFETY: null security/name creates a new unnamed, non-inheritable job.
    let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if handle.is_null() {
        return Err(std::io::Error::last_os_error().into());
    }
    // SAFETY: this is the newly created handle, owned exactly once.
    let job = unsafe { OwnedHandle::from_raw_handle(handle) };
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: initialized limits is readable for the exact size; job is still owned.
    if unsafe {
        SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of_val(&limits) as u32,
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    // SAFETY: attach this CLI before spawning anything, so all delegated descendants
    // inherit the job without a spawn/assign race. Failure is explicit, never bypassed.
    if unsafe { AssignProcessToJobObject(job.as_raw_handle(), GetCurrentProcess()) } == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    // This job includes the CLI itself: keep its sole handle until process teardown.
    // Closing it earlier would terminate this CLI before it returns the Python code.
    std::mem::forget(job);
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FileRecord {
    size: u64,
    sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format: u32,
    framework_version: String,
    machine: String,
    cli: FileRecord,
    host: FileRecord,
}

pub enum Action {
    Done(i32),
    Run(Vec<OsString>),
}

fn regular(path: &Path) -> Result<Vec<u8>, Box<dyn Error>> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.file_type().is_symlink() || meta.len() > 128 * 1024 * 1024 {
        return Err(format!("expected bounded regular file: {}", path.display()).into());
    }
    Ok(fs::read(path)?)
}

pub fn pe_subsystem(data: &[u8]) -> Result<u16, Box<dyn Error>> {
    let read16 = |at: usize| {
        data.get(at..at + 2)
            .map(|s| u16::from_le_bytes([s[0], s[1]]))
    };
    if data.get(..2) != Some(b"MZ") {
        return Err("expected Windows x64 PE32+ executable".into());
    }
    let offset = data.get(60..64).ok_or("truncated PE header")?;
    let offset = u32::from_le_bytes(offset.try_into()?) as usize;
    if offset > 1024 * 1024
        || data.get(offset..offset + 4) != Some(b"PE\0\0")
        || read16(offset + 4) != Some(0x8664)
        || read16(offset + 24) != Some(0x20b)
    {
        return Err("expected Windows x64 PE32+ executable".into());
    }
    read16(offset + 92).ok_or_else(|| "truncated PE subsystem".into())
}

fn verify(path: &Path, record: &FileRecord, subsystem: u16) -> Result<(), Box<dyn Error>> {
    let data = regular(path)?;
    if data.len() as u64 != record.size || format!("{:x}", Sha256::digest(&data)) != record.sha256 {
        return Err(format!(
            "native executable hash mismatch: {}; reinstall the Windows wheel",
            path.display()
        )
        .into());
    }
    if pe_subsystem(&data)? != subsystem {
        return Err("native PE subsystem mismatch".into());
    }
    Ok(())
}

fn environment(executable: &Path) -> Result<PathBuf, Box<dyn Error>> {
    let scripts = executable.parent().ok_or("CLI has no parent")?;
    let venv = scripts.parent().ok_or("CLI has no venv")?;
    if !scripts
        .file_name()
        .is_some_and(|n| n.eq_ignore_ascii_case("Scripts"))
        || !venv.join("pyvenv.cfg").is_file()
    {
        return Err("native CLI requires installation in a venv (pip --user/system installs are unsupported)".into());
    }
    let cfg = fs::read_to_string(venv.join("pyvenv.cfg"))?;
    if !cfg
        .lines()
        .any(|line| line.trim().starts_with("version = 3.14."))
    {
        return Err("native CLI requires a CPython 3.14 normal-GIL venv".into());
    }
    if std::env::consts::ARCH != "x86_64" {
        return Err("native CLI supports Windows x64 only".into());
    }
    let native = venv.join("Lib/site-packages/pyyaki/_native/windows");
    let manifest: Manifest = serde_json::from_slice(&regular(&native.join("manifest.json"))?)?;
    if manifest.format != 1 || manifest.framework_version != VERSION || manifest.machine != "AMD64"
    {
        return Err("native manifest version/CPU mismatch; reinstall the Windows wheel".into());
    }
    let metadata = venv.join(format!(
        "Lib/site-packages/pyyaki-{VERSION}.dist-info/METADATA"
    ));
    if !fs::read_to_string(metadata)?
        .lines()
        .any(|line| line == format!("Version: {VERSION}"))
    {
        return Err("installed framework version mismatch".into());
    }
    verify(executable, &manifest.cli, 3)?;
    verify(&native.join("pyyaki-host.exe"), &manifest.host, 2)?;
    let python = scripts.join("python.exe");
    if pe_subsystem(&regular(&python)?)? != 3 {
        return Err("invalid venv Python executable".into());
    }
    Ok(python)
}

pub fn dispatch(mut args: Vec<OsString>) -> Result<Action, Box<dyn Error>> {
    match args.first().and_then(|s| s.to_str()) {
        Some("--version") if args.len() == 1 => {
            println!("PyYaki {VERSION}");
            return Ok(Action::Done(0));
        }
        None | Some("--help" | "-h") => {
            println!(
                "PyYaki {VERSION}\nUsage: pyyaki <dev|run|build> [options]\nrun [--project PATH] [--smoke-test|--diagnostics]\nWindows native wheel: CPython 3.14 normal-GIL x64 venv required."
            );
            return Ok(Action::Done(0));
        }
        Some("run") if args.len() == 2 && (args[1] == "--help" || args[1] == "-h") => {
            println!(
                "Usage: pyyaki run [--project PATH] [--smoke-test|--diagnostics]\nPython is selected from this CLI's venv."
            );
            return Ok(Action::Done(0));
        }
        Some("run" | "dev" | "build") => {}
        _ => return Err("unknown command; use pyyaki --help".into()),
    }
    let python = environment(&std::env::current_exe()?)?;
    if args[0] == "run" {
        if args.iter().any(|arg| arg == "--python") {
            return Err("installed native CLI selects its own venv; --python is supported only by the maintainer host".into());
        }
        args.push("--python".into());
        args.push(python.into_os_string());
        return Ok(Action::Run(args));
    }
    own_delegation_job()?;
    install_console_handler(true)?;
    let status = Command::new(python)
        .args(["-I", "-m", "pyyaki.cli"])
        .args(args)
        .status()?;
    Ok(Action::Done(status.code().unwrap_or(1)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pe(subsystem: u16) -> Vec<u8> {
        let mut data = vec![0; 256];
        data[..2].copy_from_slice(b"MZ");
        data[60..64].copy_from_slice(&64u32.to_le_bytes());
        data[64..70].copy_from_slice(b"PE\0\0\x64\x86");
        data[88..90].copy_from_slice(&0x20bu16.to_le_bytes());
        data[156..158].copy_from_slice(&subsystem.to_le_bytes());
        data
    }

    #[test]
    fn venv_resolution_and_damage_fail_before_python_execution() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("test-results")
            .join(format!(
                "native-cli-unit-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
        let scripts = root.join("日本語 space/Scripts");
        let venv = scripts.parent().unwrap();
        let native = venv.join("Lib/site-packages/pyyaki/_native/windows");
        fs::create_dir_all(&scripts).unwrap();
        fs::create_dir_all(&native).unwrap();
        let metadata = venv.join(format!("Lib/site-packages/pyyaki-{VERSION}.dist-info"));
        fs::create_dir_all(&metadata).unwrap();
        fs::write(metadata.join("METADATA"), format!("Version: {VERSION}\n")).unwrap();
        fs::write(venv.join("pyvenv.cfg"), "version = 3.14.10\n").unwrap();
        let exe = scripts.join("pyyaki.exe");
        let host = native.join("pyyaki-host.exe");
        fs::write(&exe, pe(3)).unwrap();
        fs::write(scripts.join("python.exe"), pe(3)).unwrap();
        fs::write(&host, pe(2)).unwrap();
        let record = |data: Vec<u8>| serde_json::json!({"size":data.len(), "sha256":format!("{:x}", Sha256::digest(data))});
        let manifest = serde_json::json!({"format":1,"framework_version":VERSION,"machine":"AMD64",
            "cli":record(pe(3)),"host":record(pe(2))});
        let path = native.join("manifest.json");
        fs::write(&path, manifest.to_string()).unwrap();
        assert_eq!(environment(&exe).unwrap(), scripts.join("python.exe"));
        fs::write(&host, b"damaged").unwrap();
        assert!(
            environment(&exe)
                .unwrap_err()
                .to_string()
                .contains("hash mismatch")
        );
        fs::write(&host, pe(2)).unwrap();
        for key in ["machine", "framework_version"] {
            let mut bad = manifest.clone();
            bad[key] = "wrong".into();
            fs::write(&path, bad.to_string()).unwrap();
            assert!(
                environment(&exe)
                    .unwrap_err()
                    .to_string()
                    .contains("version/CPU mismatch")
            );
        }
        fs::write(&path, manifest.to_string()).unwrap();
        fs::write(venv.join("pyvenv.cfg"), "version = 3.13.0\n").unwrap();
        assert!(
            environment(&exe)
                .unwrap_err()
                .to_string()
                .contains("CPython 3.14")
        );
        assert!(
            environment(&root.join("pyyaki.exe"))
                .unwrap_err()
                .to_string()
                .contains("venv")
        );
    }

    #[test]
    fn pe_rejects_truncation_overflow_and_wrong_cpu() {
        for mut data in [vec![], b"MZ".to_vec(), pe(3)[..100].to_vec(), pe(3)] {
            if data.len() == 256 {
                data[60..64].copy_from_slice(&u32::MAX.to_le_bytes());
            }
            assert!(pe_subsystem(&data).is_err());
        }
        let mut arm = pe(3);
        arm[68..70].copy_from_slice(&0xaa64u16.to_le_bytes());
        assert!(pe_subsystem(&arm).is_err());
    }
}
