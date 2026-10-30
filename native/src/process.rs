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
            "-m".encode_utf16().collect(),
            "pyyaki.worker".encode_utf16().collect(),
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
        // SAFETY: process is suspended, job and thread are valid and exclusively owned here.
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
