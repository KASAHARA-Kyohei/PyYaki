//! Dedicated inherited pipes and a private process group for the macOS worker.
use std::{
    fs::File,
    io,
    os::{
        fd::{AsRawFd, OwnedFd},
        unix::process::CommandExt,
    },
    path::Path,
    process::{Child, Command, Stdio},
};

pub struct Worker {
    child: Child,
    pub read: File,
    pub write: File,
}

impl Worker {
    pub fn spawn(python: &Path, project: &Path, log: &Path) -> io::Result<Self> {
        let (child_read, host_write) = io::pipe()?;
        let (host_read, child_write) = io::pipe()?;
        let input = child_read.as_raw_fd();
        let output = child_write.as_raw_fd();
        let log = File::create_new(log)?;
        let mut command = Command::new(python);
        command
            .args(["-I", "-m", "pyyaki.worker", "--mode", "macos", "--project"])
            .arg(project)
            .arg("--read-handle")
            .arg(input.to_string())
            .arg("--write-handle")
            .arg(output.to_string())
            .current_dir(project)
            .stdin(Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log)
            .process_group(0);
        // SAFETY: only async-signal-safe fcntl calls run between fork and exec;
        // both descriptors are owned and live until spawn returns. Parent flags stay unchanged.
        unsafe {
            command.pre_exec(move || {
                for fd in [input, output] {
                    if libc::fcntl(fd, libc::F_SETFD, 0) == -1 {
                        return Err(io::Error::last_os_error());
                    }
                }
                Ok(())
            });
        }
        let child = command.spawn()?;
        let read: OwnedFd = host_read.into();
        let write: OwnedFd = host_write.into();
        Ok(Self {
            child,
            read: read.into(),
            write: write.into(),
        })
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
            // SAFETY: spawn created a private process group whose leader is this owned child.
            // Kill the group before reaping the leader so its PID cannot have been reused.
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
        let worker = Worker { child, read, write };
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
