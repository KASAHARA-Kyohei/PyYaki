//! Dedicated inherited pipes and a private process group for the macOS worker.
use std::{
    fs::File,
    io::{self, Read},
    os::{
        fd::{AsRawFd, OwnedFd, RawFd},
        unix::process::CommandExt,
    },
    path::Path,
    process::{Child, Command, Stdio},
};

pub struct Worker {
    child: Child,
    _lifetime: io::PipeWriter,
    worker_pid: u32,
    pub read: File,
    pub write: File,
}

pub fn inherit_fds<const N: usize>(command: &mut Command, fds: [RawFd; N]) {
    // SAFETY: only async-signal-safe fcntl calls run between fork and exec.
    // Callers keep all FDs live until spawn returns; parent flags do not change.
    unsafe {
        command.pre_exec(move || {
            for fd in fds {
                if libc::fcntl(fd, libc::F_SETFD, 0) == -1 {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
}

impl Worker {
    pub fn spawn(python: &Path, project: &Path, log: &Path) -> io::Result<Self> {
        let (child_read, host_write) = io::pipe()?;
        let (host_read, child_write) = io::pipe()?;
        let (guard_lifetime, lifetime) = io::pipe()?;
        let (mut ready, guard_status) = io::pipe()?;
        let input = child_read.as_raw_fd();
        let output = child_write.as_raw_fd();
        let log = File::create_new(log)?;
        let mut command = Command::new(std::env::current_exe()?);
        command
            .arg(crate::guard_macos::ARG)
            .arg(input.to_string())
            .arg(output.to_string())
            .arg(guard_lifetime.as_raw_fd().to_string())
            .arg(guard_status.as_raw_fd().to_string())
            .arg(python)
            .arg(project)
            .current_dir(project)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .process_group(0);
        inherit_fds(
            &mut command,
            [
                input,
                output,
                guard_lifetime.as_raw_fd(),
                guard_status.as_raw_fd(),
            ],
        );
        let child = command.spawn()?;
        let read: OwnedFd = host_read.into();
        let write: OwnedFd = host_write.into();
        let mut worker = Self {
            child,
            _lifetime: lifetime,
            worker_pid: 0,
            read: read.into(),
            write: write.into(),
        };
        drop((child_read, child_write, guard_lifetime, guard_status));
        let mut pid = [0; 4];
        ready.read_exact(&mut pid)?;
        worker.worker_pid = u32::from_le_bytes(pid);
        Ok(worker)
    }
    pub fn pid(&self) -> u32 {
        self.worker_pid
    }
    pub fn guard_pid(&self) -> u32 {
        self.child.id()
    }
    pub fn exited(&self) -> bool {
        let child = &self.child;
        // SAFETY: zero is a valid empty siginfo_t; waitid writes into this live allocation.
        // WNOWAIT deliberately leaves the child unreaped, retaining its PID until Drop.
        unsafe {
            let mut info: libc::siginfo_t = std::mem::zeroed();
            libc::waitid(
                libc::P_PID,
                child.id(),
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            ) == 0
                && info.si_pid != 0
                // Darwin may report CLD_STOPPED even with WEXITED; a stopped child is alive.
                && matches!(info.si_code, libc::CLD_EXITED | libc::CLD_KILLED | libc::CLD_DUMPED)
        }
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        {
            let child = &mut self.child;
            // SAFETY: spawn created a private group led by the owned guard child.
            // Its PID remains reserved (including after exit) until wait below.
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGKILL);
            }
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        thread,
        time::{Duration, Instant},
    };

    #[test]
    fn stopped_worker_is_not_exited_and_killed_worker_is() {
        let read = File::open("/dev/null").unwrap();
        let write = read.try_clone().unwrap();
        let child = Command::new("/bin/sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let (_, lifetime) = io::pipe().unwrap();
        let worker = Worker {
            worker_pid: child.id(),
            child,
            _lifetime: lifetime,
            read,
            write,
        };
        // SAFETY: the owned, unreaped child retains its PID. siginfo_t is a live zeroed buffer.
        unsafe {
            assert_eq!(libc::kill(worker.child.id() as i32, libc::SIGSTOP), 0);
            let mut info: libc::siginfo_t = std::mem::zeroed();
            assert_eq!(
                libc::waitid(
                    libc::P_PID,
                    worker.child.id(),
                    &mut info,
                    libc::WSTOPPED | libc::WNOWAIT
                ),
                0
            );
            assert_eq!(info.si_code, libc::CLD_STOPPED);
        }
        assert!(!worker.exited());
        // SAFETY: child is still owned and unreaped; SIGKILL also terminates stopped children.
        unsafe {
            assert_eq!(libc::kill(worker.child.id() as i32, libc::SIGKILL), 0);
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while !worker.exited() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        assert!(worker.exited());
    }
}
