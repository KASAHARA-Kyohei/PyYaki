//! Windows worker process ownership and restricted handle inheritance.

use pyyaki_host::command_line::quote;

use std::{
    fs::{File, OpenOptions},
    io,
    mem::size_of,
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::Path,
    ptr::{null, null_mut},
};
use windows_sys::Win32::{
    Foundation::{
        DUPLICATE_SAME_ACCESS, DuplicateHandle, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
        SetHandleInformation, WAIT_OBJECT_0,
    },
    Security::SECURITY_ATTRIBUTES,
    System::{
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject,
        },
        Pipes::CreatePipe,
        Threading::{
            CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessW,
            DeleteProcThreadAttributeList, EXTENDED_STARTUPINFO_PRESENT, GetCurrentProcess,
            InitializeProcThreadAttributeList, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            PROCESS_INFORMATION, ResumeThread, STARTF_USESTDHANDLES, STARTUPINFOEXW,
            TerminateProcess, UpdateProcThreadAttribute, WaitForSingleObject,
        },
    },
};

fn checked(result: i32) -> io::Result<()> {
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn own(handle: HANDLE) -> io::Result<OwnedHandle> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: callers pass a newly created, valid owned Win32 handle exactly once.
    Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
}

fn pipe() -> io::Result<(OwnedHandle, OwnedHandle)> {
    let mut read = null_mut();
    let mut write = null_mut();
    let security = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: 1,
    };
    // SAFETY: both output pointers and security structure live through the call.
    checked(unsafe { CreatePipe(&mut read, &mut write, &security, 0) })?;
    Ok((own(read)?, own(write)?))
}

fn duplicate(file: &File) -> io::Result<OwnedHandle> {
    let mut result = null_mut();
    // SAFETY: the source File owns a valid handle; the output is a fresh owned duplicate.
    unsafe {
        let current = GetCurrentProcess();
        checked(DuplicateHandle(
            current,
            file.as_raw_handle(),
            current,
            &mut result,
            0,
            1,
            DUPLICATE_SAME_ACCESS,
        ))?;
    }
    own(result)
}

struct Attributes {
    memory: Vec<usize>,
}
impl Attributes {
    fn new(handles: &[HANDLE]) -> io::Result<Self> {
        let mut bytes = 0;
        // SAFETY: a null first call queries the required allocation size.
        unsafe {
            InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut bytes);
        }
        if bytes == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut memory = vec![0; bytes.div_ceil(size_of::<usize>())];
        // SAFETY: aligned allocation covers the queried size; initialization precedes Drop.
        checked(unsafe {
            InitializeProcThreadAttributeList(memory.as_mut_ptr().cast(), 1, 0, &mut bytes)
        })?;
        let mut result = Self { memory };
        // SAFETY: all inherited handles and the backing slice stay alive through CreateProcessW.
        checked(unsafe {
            UpdateProcThreadAttribute(
                result.pointer(),
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                handles.as_ptr().cast(),
                size_of_val(handles),
                null_mut(),
                null(),
            )
        })?;
        Ok(result)
    }
    fn pointer(&mut self) -> *mut std::ffi::c_void {
        self.memory.as_mut_ptr().cast()
    }
}
impl Drop for Attributes {
    fn drop(&mut self) {
        // SAFETY: memory is still allocated and contains one initialized attribute list.
        unsafe {
            DeleteProcThreadAttributeList(self.pointer());
        }
    }
}

fn wide(path: &Path) -> io::Result<Vec<u16>> {
    let mut value: Vec<_> = path.as_os_str().encode_wide().collect();
    if value.contains(&0) {
        return Err(io::Error::other("path contains NUL"));
    }
    value.push(0);
    Ok(value)
}

pub struct Worker {
    pub process: OwnedHandle,
    _job: OwnedHandle,
    pub read: File,
    pub write: File,
}

fn assign_and_resume(
    job: &OwnedHandle,
    process: &OwnedHandle,
    thread: &OwnedHandle,
) -> io::Result<()> {
    // SAFETY: the caller owns these handles and the newly created process is suspended.
    unsafe {
        if AssignProcessToJobObject(job.as_raw_handle(), process.as_raw_handle()) == 0 {
            let error = io::Error::last_os_error();
            TerminateProcess(process.as_raw_handle(), 1);
            return Err(error);
        }
        if ResumeThread(thread.as_raw_handle()) == u32::MAX {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

impl Worker {
    pub fn spawn(python: &Path, project: &Path, log: &Path) -> io::Result<Self> {
        let (child_read, host_write) = pipe()?;
        let (host_read, child_write) = pipe()?;
        // SAFETY: parent ends are valid handles; removing inheritance cannot invalidate them.
        unsafe {
            checked(SetHandleInformation(
                host_read.as_raw_handle(),
                HANDLE_FLAG_INHERIT,
                0,
            ))?;
            checked(SetHandleInformation(
                host_write.as_raw_handle(),
                HANDLE_FLAG_INHERIT,
                0,
            ))?;
        }
        let logfile = OpenOptions::new().create_new(true).write(true).open(log)?;
        let child_log = duplicate(&logfile)?;
        let child_stdin = duplicate(&File::open("NUL")?)?;
        let handles = [
            child_read.as_raw_handle(),
            child_write.as_raw_handle(),
            child_log.as_raw_handle(),
            child_stdin.as_raw_handle(),
        ];
        let mut attributes = Attributes::new(&handles)?;
        let program = wide(&python.canonicalize()?)?;
        let directory = wide(project)?;
        let arguments: Vec<Vec<u16>> = vec![
            program[..program.len() - 1].to_vec(),
            "-I".encode_utf16().collect(),
            "-B".encode_utf16().collect(),
            "-c".encode_utf16().collect(),
            include_str!("../worker_bootstrap.py")
                .encode_utf16()
                .collect(),
            "--project".encode_utf16().collect(),
            directory[..directory.len() - 1].to_vec(),
            "--read-handle".encode_utf16().collect(),
            (child_read.as_raw_handle() as usize)
                .to_string()
                .encode_utf16()
                .collect(),
            "--write-handle".encode_utf16().collect(),
            (child_write.as_raw_handle() as usize)
                .to_string()
                .encode_utf16()
                .collect(),
        ];
        let mut command = Vec::new();
        for argument in arguments {
            if !command.is_empty() {
                command.push(b' ' as u16);
            }
            command.extend(quote(&argument));
        }
        command.push(0);
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = child_stdin.as_raw_handle();
        startup.StartupInfo.hStdOutput = child_log.as_raw_handle();
        startup.StartupInfo.hStdError = child_log.as_raw_handle();
        startup.lpAttributeList = attributes.pointer();
        // SAFETY: null security/name uses default ACLs and a fresh unnamed job.
        let job = own(unsafe { CreateJobObjectW(null(), null()) })?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: initialized limits is readable for the exact size passed.
        checked(unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of_val(&limits) as u32,
            )
        })?;
        let mut info = PROCESS_INFORMATION::default();
        // SAFETY: UTF-16 buffers, attributes and whitelisted handles all live until return.
        checked(unsafe {
            CreateProcessW(
                program.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                1,
                CREATE_SUSPENDED
                    | CREATE_NO_WINDOW
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                null(),
                directory.as_ptr(),
                &startup.StartupInfo,
                &mut info,
            )
        })?;
        let process = own(info.hProcess)?;
        let thread = own(info.hThread)?;
        assign_and_resume(&job, &process, &thread)?;
        Ok(Self {
            process,
            _job: job,
            read: host_read.into(),
            write: host_write.into(),
        })
    }

    pub fn exited(&self) -> bool {
        // SAFETY: Worker owns the process handle for the entire call; zero timeout never blocks.
        unsafe { WaitForSingleObject(self.process.as_raw_handle(), 0) == WAIT_OBJECT_0 }
    }
}

/// Owns the GUI child of a onefile launcher, including its nested worker Job.
/// No handles are inherited; environment changes are confined to this child.
pub struct HostChild {
    process: OwnedHandle,
    _job: OwnedHandle,
    pub pid: u32,
}

impl HostChild {
    pub fn spawn(
        executable: &Path,
        cache: &Path,
        mode: Option<&std::ffi::OsStr>,
    ) -> io::Result<Self> {
        use windows_sys::Win32::System::Threading::STARTUPINFOW;
        let program = wide(executable)?;
        let mut arguments = vec![
            executable.as_os_str().to_os_string(),
            "run".into(),
            "--project".into(),
            cache.join("app").into_os_string(),
            "--python".into(),
            cache.join("runtime/python/python.exe").into_os_string(),
        ];
        if let Some(mode) = mode {
            arguments.push(mode.into());
        }
        let mut command = Vec::new();
        for argument in arguments {
            if !command.is_empty() {
                command.push(32);
            }
            command.extend(quote(&argument.encode_wide().collect::<Vec<_>>()));
        }
        command.push(0);
        let mut environment: Vec<(std::ffi::OsString, std::ffi::OsString)> = std::env::vars_os()
            .filter(|(key, _)| {
                !key.to_string_lossy()
                    .to_ascii_uppercase()
                    .starts_with("WEBVIEW2_")
            })
            .collect();
        environment.push((
            "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER".into(),
            cache.join("runtime/webview2").into_os_string(),
        ));
        environment.sort_by_key(|(key, _)| key.to_string_lossy().to_ascii_uppercase());
        let mut block = Vec::new();
        for (mut key, value) in environment {
            key.push("=");
            key.push(value);
            block.extend(key.encode_wide());
            block.push(0);
        }
        block.push(0);
        // SAFETY: null name/security creates a new private job with the token's default ACL.
        let job = own(unsafe { CreateJobObjectW(null(), null()) })?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: readable initialized limits of the exact stated size.
        checked(unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of_val(&limits) as u32,
            )
        })?;
        let startup = STARTUPINFOW {
            cb: size_of::<STARTUPINFOW>() as u32,
            ..Default::default()
        };
        let mut info = PROCESS_INFORMATION::default();
        let cwd = wide(cache)?;
        // SAFETY: all buffers live through CreateProcessW; no handles are inherited.
        checked(unsafe {
            CreateProcessW(
                program.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                0,
                CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                block.as_ptr().cast(),
                cwd.as_ptr(),
                &startup,
                &mut info,
            )
        })?;
        let process = own(info.hProcess)?;
        let thread = own(info.hThread)?;
        assign_and_resume(&job, &process, &thread)?;
        Ok(Self {
            process,
            _job: job,
            pid: info.dwProcessId,
        })
    }

    pub fn exit_code(&self) -> io::Result<Option<u32>> {
        use windows_sys::Win32::System::Threading::GetExitCodeProcess;
        // SAFETY: owned process handle is valid; zero timeout does not block.
        let state = unsafe { WaitForSingleObject(self.process.as_raw_handle(), 0) };
        if state == windows_sys::Win32::Foundation::WAIT_TIMEOUT {
            return Ok(None);
        }
        if state != WAIT_OBJECT_0 {
            return Err(io::Error::last_os_error());
        }
        let mut code = 0;
        // SAFETY: output points to a live u32 and process is still owned.
        checked(unsafe { GetExitCodeProcess(self.process.as_raw_handle(), &mut code) })?;
        Ok(Some(code))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{env, fs, time::SystemTime};
    use windows_sys::Win32::System::Threading::{GetExitCodeProcess, STARTUPINFOW};

    struct Probe {
        process: OwnedHandle,
        thread: OwnedHandle,
        pid: u32,
    }

    impl Drop for Probe {
        fn drop(&mut self) {
            // SAFETY: only the child created below is targeted, including on a failed assertion.
            unsafe { TerminateProcess(self.process.as_raw_handle(), 1) };
        }
    }

    fn suspended_probe(marker: &Path) -> Probe {
        let executable = wide(&env::current_exe().unwrap()).unwrap();
        let mut command = quote(&executable[..executable.len() - 1]);
        command.extend(" --ignored --exact process::tests::assignment_probe_child".encode_utf16());
        command.push(0);
        let mut entries: Vec<Vec<u16>> = env::vars_os()
            .filter(|(key, _)| {
                !key.to_string_lossy()
                    .eq_ignore_ascii_case("PYYAKI_JOB_CHILD_MARKER")
            })
            .map(|(mut key, value)| {
                key.push("=");
                key.push(value);
                key.encode_wide().chain(Some(0)).collect()
            })
            .collect();
        let mut entry = std::ffi::OsString::from("PYYAKI_JOB_CHILD_MARKER=");
        entry.push(marker);
        entries.push(entry.encode_wide().chain(Some(0)).collect());
        entries.sort_by_key(|entry| String::from_utf16_lossy(entry).to_uppercase());
        let mut environment: Vec<u16> = entries.into_iter().flatten().collect();
        environment.push(0);
        let startup = STARTUPINFOW {
            cb: size_of::<STARTUPINFOW>() as u32,
            ..Default::default()
        };
        let mut info = PROCESS_INFORMATION::default();
        // SAFETY: buffers live through CreateProcessW; no handles are inherited by this test child.
        checked(unsafe {
            CreateProcessW(
                executable.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                0,
                CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                environment.as_ptr().cast(),
                null(),
                &startup,
                &mut info,
            )
        })
        .unwrap();
        Probe {
            process: own(info.hProcess).unwrap(),
            thread: own(info.hThread).unwrap(),
            pid: info.dwProcessId,
        }
    }

    #[test]
    #[ignore = "child entry, invoked explicitly by the job assignment test"]
    fn assignment_probe_child() {
        let path = env::var_os("PYYAKI_JOB_CHILD_MARKER").expect("explicit child marker required");
        fs::write(path, b"child executed").unwrap();
    }

    #[test]
    fn assignment_failure_terminates_without_running_child() {
        let id = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let folder = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("test-results")
            .join(format!("windows-job-{}-{id}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        let positive_marker = folder.join("positive.txt");
        let positive = suspended_probe(&positive_marker);
        // SAFETY: unnamed job with default security, exclusively owned by this test.
        let job = own(unsafe { CreateJobObjectW(null(), null()) }).unwrap();
        assign_and_resume(&job, &positive.process, &positive.thread).unwrap();
        // SAFETY: a bounded wait on the owned child handle.
        let positive_wait = unsafe { WaitForSingleObject(positive.process.as_raw_handle(), 5000) };
        assert_eq!(positive_wait, WAIT_OBJECT_0);
        assert!(
            positive_marker.is_file(),
            "positive control must execute the child entry"
        );

        let negative_marker = folder.join("must-not-run.txt");
        let negative = suspended_probe(&negative_marker);
        // A valid file handle is deliberately the wrong object type for a Job Object.
        let not_a_job: OwnedHandle = File::open("NUL").unwrap().into();
        let error = assign_and_resume(&not_a_job, &negative.process, &negative.thread).unwrap_err();
        // SAFETY: waiting and reading the exit code of our own child only.
        let (wait, code) = unsafe {
            let wait = WaitForSingleObject(negative.process.as_raw_handle(), 5000);
            let mut code = 0;
            checked(GetExitCodeProcess(
                negative.process.as_raw_handle(),
                &mut code,
            ))
            .unwrap();
            (wait, code)
        };
        assert_eq!(
            wait, WAIT_OBJECT_0,
            "suspended process must be terminated on assignment failure"
        );
        assert_eq!(code, 1);
        assert!(!negative_marker.exists());
        fs::write(
            folder.join("result.json"),
            serde_json::to_vec_pretty(&serde_json::json!({
                "positive_pid": positive.pid, "positive_executed": true,
                "negative_pid": negative.pid, "negative_executed": false,
                "assignment_os_error": error.raw_os_error(), "negative_exit_code": code,
                "negative_terminated": true,
            }))
            .unwrap(),
        )
        .unwrap();
    }
}
