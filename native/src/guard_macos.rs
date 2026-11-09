//! A host-owned group leader survives host death and cleans its worker group.
use std::{
    ffi::OsString,
    fs::File,
    io::{self, Write},
    os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd},
    path::PathBuf,
    process::{Command, Stdio},
};

pub const ARG: &str = "--__pyyaki-worker-guard";

struct GroupExit(libc::pid_t);

impl Drop for GroupExit {
    fn drop(&mut self) {
        // SAFETY: this process is the private group leader, checked before construction.
        // Its live PID reserves the group ID. Kill this group, including ourselves;
        // never a parent, unrelated PID, or a group found by enumerating the system.
        unsafe { libc::kill(-self.0, libc::SIGKILL) };
    }
}

fn close_on_exec(fd: RawFd) -> io::Result<()> {
    // SAFETY: fd is live and exclusively owned by this guard. fcntl has no pointer arguments.
    if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } == -1 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn event(ident: usize, filter: i16, fflags: u32) -> libc::kevent {
    libc::kevent {
        ident,
        filter,
        flags: libc::EV_ADD | libc::EV_ONESHOT,
        fflags,
        data: 0,
        udata: std::ptr::null_mut(),
    }
}

fn register(queue: RawFd, change: libc::kevent) -> io::Result<()> {
    // SAFETY: change is a correctly initialized libc kevent, live for this call.
    // No output events or timeout buffer are requested.
    if unsafe { libc::kevent(queue, &change, 1, std::ptr::null_mut(), 0, std::ptr::null()) } == -1 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn notified(queue: RawFd, immediate: bool) -> io::Result<bool> {
    let mut notification = event(0, 0, 0);
    let zero = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    loop {
        // SAFETY: queue is owned and live. notification is a valid writable kevent;
        // zero is a live timeout, or null requests a blocking wait. No changes are supplied.
        let count = unsafe {
            libc::kevent(
                queue,
                std::ptr::null(),
                0,
                &mut notification,
                1,
                if immediate { &zero } else { std::ptr::null() },
            )
        };
        if count >= 0 {
            if count > 0 && notification.flags & libc::EV_ERROR != 0 {
                return Err(io::Error::from_raw_os_error(notification.data as i32));
            }
            return Ok(count != 0);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

fn serve(fds: [RawFd; 4], python: PathBuf, project: PathBuf) -> io::Result<()> {
    for fd in fds {
        close_on_exec(fd)?;
    }
    // SAFETY: the private entry point received exactly these distinct, validated pipe FDs
    // from exec. Ownership is transferred once and retained until the guard exits.
    let [input, output, lifetime, status] = fds.map(|fd| unsafe { OwnedFd::from_raw_fd(fd) });
    let mut status = File::from(status);
    // SAFETY: kqueue has no pointer arguments; a successful return is a new owned FD.
    let raw_queue = unsafe { libc::kqueue() };
    if raw_queue == -1 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: raw_queue is the newly returned FD and has no other Rust owner.
    let queue = unsafe { OwnedFd::from_raw_fd(raw_queue) };
    close_on_exec(queue.as_raw_fd())?;
    register(
        queue.as_raw_fd(),
        event(lifetime.as_raw_fd() as usize, libc::EVFILT_READ, 0),
    )?;
    if notified(queue.as_raw_fd(), true)? {
        return Ok(()); // Host already died; do not start Python.
    }
    let mut command = Command::new(python);
    command
        .args([
            "-I",
            "-B",
            "-c",
            include_str!("../worker_bootstrap.py"),
            "--mode",
            "macos",
            "--project",
        ])
        .arg(project)
        .arg("--read-handle")
        .arg(input.as_raw_fd().to_string())
        .arg("--write-handle")
        .arg(output.as_raw_fd().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    // Inherit the guard's private group, not a new group. Only framing FDs survive exec.
    crate::process::inherit_fds(&mut command, [input.as_raw_fd(), output.as_raw_fd()]);
    let mut child = command.spawn()?;
    register(
        queue.as_raw_fd(),
        event(child.id() as usize, libc::EVFILT_PROC, libc::NOTE_EXIT),
    )?;
    status.write_all(&child.id().to_le_bytes())?;
    drop(status);
    eprintln!(
        "pyyaki_guard=ready guard={} worker={}",
        std::process::id(),
        child.id()
    );
    notified(queue.as_raw_fd(), false)?;
    // Reap an already exited Python when possible; the live guard still anchors its PGID.
    let _ = child.try_wait();
    Ok(())
}

pub fn run() -> io::Result<()> {
    let args: Vec<OsString> = std::env::args_os().skip(2).collect();
    if args.len() != 6 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "private guard arguments required",
        ));
    }
    let mut fds = [0; 4];
    for (index, value) in args[..4].iter().enumerate() {
        fds[index] = value
            .to_str()
            .and_then(|s| s.parse().ok())
            .filter(|fd| *fd > 2)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid guard FD"))?;
        if fds[..index].contains(&fds[index]) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "duplicate guard FD",
            ));
        }
        // SAFETY: zero is a valid initial stat; fstat initializes this live allocation.
        let mut info: libc::stat = unsafe { std::mem::zeroed() };
        // SAFETY: info points to a writable stat; fstat checks the supplied FD.
        if unsafe { libc::fstat(fds[index], &mut info) } == -1
            || info.st_mode & libc::S_IFMT != libc::S_IFIFO
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "guard requires inherited pipes",
            ));
        }
    }
    // SAFETY: getpid/getpgrp have no pointer arguments and do not mutate process state.
    let (pid, group) = unsafe { (libc::getpid(), libc::getpgrp()) };
    if pid <= 1 || pid != group {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "guard requires its private group",
        ));
    }
    let group = GroupExit(pid);
    if let Err(error) = serve(fds, PathBuf::from(&args[4]), PathBuf::from(&args[5])) {
        eprintln!("pyyaki_guard=failed {error}");
    }
    drop(group); // Includes this guard; no user work or Python runs after this point.
    std::process::abort(); // Fail closed if self-SIGKILL could not be delivered.
}

#[cfg(test)]
mod tests {
    use super::*;

    fn queue() -> OwnedFd {
        // SAFETY: kqueue has no pointer arguments and returns a fresh owned descriptor.
        let fd = unsafe { libc::kqueue() };
        assert!(fd >= 0);
        // SAFETY: fd was just created and has no other owner.
        unsafe { OwnedFd::from_raw_fd(fd) }
    }

    #[test]
    fn lifetime_is_notified_when_its_only_writer_closes() {
        let (read, write) = io::pipe().unwrap();
        let queue = queue();
        register(
            queue.as_raw_fd(),
            event(read.as_raw_fd() as usize, libc::EVFILT_READ, 0),
        )
        .unwrap();
        assert!(!notified(queue.as_raw_fd(), true).unwrap());
        drop(write);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while !notified(queue.as_raw_fd(), true).unwrap() {
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }

    #[test]
    fn stopped_child_is_not_reported_as_exited() {
        struct Reap(std::process::Child);
        impl Drop for Reap {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let mut child = Reap(Command::new("/bin/sleep").arg("30").spawn().unwrap());
        let queue = queue();
        register(
            queue.as_raw_fd(),
            event(child.0.id() as usize, libc::EVFILT_PROC, libc::NOTE_EXIT),
        )
        .unwrap();
        // SAFETY: child is owned and unreaped; its PID cannot have been reused.
        assert_eq!(unsafe { libc::kill(child.0.id() as i32, libc::SIGSTOP) }, 0);
        assert!(!notified(queue.as_raw_fd(), true).unwrap());
        child.0.kill().unwrap();
        assert!(notified(queue.as_raw_fd(), false).unwrap());
    }
}
