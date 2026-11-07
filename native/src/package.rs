//! Bounded stored-blob format for local Windows onefile prototypes.
//! SHA-256 detects damage; it is not proof of authorship or an Authenticode check.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{self, Read, Seek, SeekFrom, Write},
    os::windows::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt},
    },
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};

pub const MAGIC: &[u8; 8] = b"PYYAKI01";
const LIMIT: u64 = 2 * 1024 * 1024 * 1024;
const INDEX_LIMIT: u64 = 8 * 1024 * 1024;
const FILE_LIMIT: u64 = 512 * 1024 * 1024;

fn invalid(text: &str) -> io::Error {
    io::Error::other(text)
}
fn wide(path: &Path) -> Vec<u16> {
    path.as_os_str().encode_wide().chain([0]).collect()
}
fn check_cancel(cancelled: &AtomicBool) -> io::Result<()> {
    if cancelled.load(Ordering::Acquire) {
        Err(invalid("Preparation cancelled"))
    } else {
        Ok(())
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub path: String,
    pub offset: u64,
    pub size: u64,
    pub sha256: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Index {
    pub format: u32,
    pub framework_version: String,
    pub app_id: String,
    pub host_size: u64,
    pub files: Vec<Entry>,
}
pub struct Package {
    file: File,
    pub index: Index,
    pub id: String,
}

fn read_at<const N: usize>(file: &mut File, at: u64) -> io::Result<[u8; N]> {
    file.seek(SeekFrom::Start(at))?;
    let mut bytes = [0; N];
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}

// Certificate directory is a file offset (not an RVA). Real signing is a separate gate.
fn pe_end(file: &mut File) -> io::Result<(u64, u64)> {
    let length = file.metadata()?.len();
    if length > LIMIT || &read_at::<2>(file, 0)? != b"MZ" {
        return Err(invalid("Not a bounded PE file"));
    }
    let pe = u32::from_le_bytes(read_at(file, 60)?) as u64;
    if pe > 1024 * 1024 || &read_at::<4>(file, pe)? != b"PE\0\0" {
        return Err(invalid("Invalid PE header"));
    }
    let sections = u16::from_le_bytes(read_at(file, pe + 6)?) as u64;
    let optional = u16::from_le_bytes(read_at(file, pe + 20)?) as u64;
    if u16::from_le_bytes(read_at(file, pe + 4)?) != 0x8664
        || !(1..=96).contains(&sections)
        || optional < 152
        || u16::from_le_bytes(read_at(file, pe + 24)?) != 0x20b
        || u32::from_le_bytes(read_at(file, pe + 132)?) < 5
    {
        return Err(invalid("Onefile requires Windows x64 PE32+"));
    }
    let table = pe + 24 + optional;
    let mut image_end = table + sections * 40;
    for n in 0..sections {
        let size = u32::from_le_bytes(read_at(file, table + n * 40 + 16)?) as u64;
        let offset = u32::from_le_bytes(read_at(file, table + n * 40 + 20)?) as u64;
        image_end = image_end.max(offset + size);
    }
    let certificate = u32::from_le_bytes(read_at(file, pe + 168)?) as u64;
    let size = u32::from_le_bytes(read_at(file, pe + 172)?) as u64;
    let end = if certificate == 0 && size == 0 {
        length
    } else {
        if certificate < image_end
            || certificate % 8 != 0
            || size < 8
            || certificate.checked_add(size) != Some(length)
        {
            return Err(invalid("Invalid PE certificate table range"));
        }
        let mut at = certificate;
        while at < length {
            let record = u32::from_le_bytes(read_at(file, at)?) as u64;
            if record < 8 || at + record > length {
                return Err(invalid("Invalid PE certificate record"));
            }
            at += (record + 7) & !7;
        }
        if at != length {
            return Err(invalid("Invalid PE certificate alignment"));
        }
        certificate
    };
    if image_end > end {
        return Err(invalid("PE sections exceed package"));
    }
    Ok((end, image_end))
}

pub fn valid_path(path: &str) -> bool {
    if path.len() > 220 || !path.is_ascii() || path.is_empty() {
        return false;
    }
    path.split('/').all(|part| {
        let base = part.split('.').next().unwrap_or("").to_ascii_uppercase();
        !part.is_empty()
            && part != "."
            && part != ".."
            && !part.ends_with(['.', ' '])
            && !part.bytes().any(|b| b < 32 || b"\\:<>\"|?*".contains(&b))
            && !matches!(
                base.as_str(),
                "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
            )
            && !(base.len() == 4
                && (base.starts_with("COM") || base.starts_with("LPT"))
                && matches!(base.as_bytes()[3], b'1'..=b'9'))
    })
}
fn valid_id(id: &str) -> bool {
    id.len() <= 120
        && id.contains('.')
        && id.split('.').all(|part| {
            !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
        })
}

impl Package {
    pub fn open(path: &Path) -> io::Result<Self> {
        let mut file = File::open(path)?;
        let (end, image_end) = pe_end(&mut file)?;
        if end < 64 {
            return Err(invalid("Missing onefile footer"));
        }
        let footer: [u8; 64] = read_at(&mut file, end - 64)?;
        if &footer[..8] != MAGIC || footer[56..] != [0; 8] {
            return Err(invalid("Missing or invalid onefile footer"));
        }
        let offset = u64::from_le_bytes(footer[8..16].try_into().unwrap());
        let size = u64::from_le_bytes(footer[16..24].try_into().unwrap());
        if !(1..=INDEX_LIMIT).contains(&size)
            || offset < image_end
            || offset.checked_add(size) != Some(end - 64)
        {
            return Err(invalid("Invalid onefile index range"));
        }
        let mut bytes = vec![0; size as usize];
        file.seek(SeekFrom::Start(offset))?;
        file.read_exact(&mut bytes)?;
        let hash = Sha256::digest(&bytes);
        if hash[..] != footer[24..56] {
            return Err(invalid("Onefile index hash mismatch"));
        }
        let index: Index = serde_json::from_slice(&bytes)?;
        if index.format != 1
            || index.framework_version != crate::VERSION
            || !valid_id(&index.app_id)
            || !(1..=10_000).contains(&index.files.len())
            || index.host_size < image_end
            || index.host_size > offset
        {
            return Err(invalid("Invalid onefile index metadata"));
        }
        let mut names = BTreeSet::new();
        let mut cursor = index.host_size;
        for entry in &index.files {
            if !valid_path(&entry.path)
                || !names.insert(entry.path.to_ascii_lowercase())
                || entry.offset != cursor
                || entry.size > FILE_LIMIT
                || entry.sha256.len() != 64
                || !entry
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            {
                return Err(invalid("Invalid onefile file record/path"));
            }
            cursor = cursor
                .checked_add(entry.size)
                .ok_or_else(|| invalid("File size overflow"))?;
            if cursor > offset {
                return Err(invalid("File exceeds payload"));
            }
        }
        for name in &names {
            let mut parent = name.as_str();
            while let Some((prefix, _)) = parent.rsplit_once('/') {
                if names.contains(prefix) {
                    return Err(invalid("File/directory collision"));
                }
                parent = prefix;
            }
        }
        if cursor != offset {
            return Err(invalid("Unindexed payload bytes"));
        }
        for required in [
            "app/pyproject.toml",
            "runtime/python/python.exe",
            "runtime/python/python314._pth",
            "runtime/webview2/msedgewebview2.exe",
            "packages/pyyaki/worker.py",
        ] {
            if !names.contains(required) {
                return Err(invalid("Missing required runtime file"));
            }
        }
        Ok(Self {
            file,
            index,
            id: format!("{hash:x}"),
        })
    }

    pub fn prepare(&mut self, base: &Path, cancelled: &AtomicBool) -> io::Result<(PathBuf, bool)> {
        let mut held = Vec::new();
        for ancestor in base.ancestors().collect::<Vec<_>>().into_iter().rev() {
            held.push(directory_guard(ancestor)?);
        }
        let private = base.join("PyYakiOnefile");
        private_directory(&private)?;
        held.push(directory_guard(&private)?);
        let app = private.join(&self.index.app_id);
        private_directory(&app)?;
        held.push(directory_guard(&app)?);
        let lock_path = app.join(format!("{}.lock", self.id));
        if lock_path.exists() {
            reject_reparse(&lock_path)?;
        }
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(lock_path)?;
        loop {
            check_cancel(cancelled)?;
            match lock.try_lock() {
                Ok(()) => break,
                Err(fs::TryLockError::WouldBlock) => {
                    std::thread::sleep(std::time::Duration::from_millis(50))
                }
                Err(fs::TryLockError::Error(error)) => return Err(error),
            }
        }
        let cache = app.join(&self.id);
        if cache.try_exists()? {
            for entry in &self.index.files {
                self.file.seek(SeekFrom::Start(entry.offset))?;
                if copy_hash(&mut self.file, None, entry.size, cancelled)? != entry.sha256 {
                    return Err(invalid("Onefile payload hash mismatch"));
                }
            }
            self.verify(&cache, cancelled)?;
            return Ok((cache, true));
        }
        let mut nonce = [0u8; 16];
        getrandom::fill(&mut nonce).map_err(|_| invalid("OS randomness unavailable"))?;
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let staging = app.join(format!("staging-{name}"));
        fs::create_dir(&staging)?;
        // Failed/interrupted staging is retained for diagnosis, never reused or auto-executed.
        for entry in &self.index.files {
            check_cancel(cancelled)?;
            let destination = staging.join(&entry.path);
            fs::create_dir_all(destination.parent().unwrap())?;
            let mut output = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(destination)?;
            self.file.seek(SeekFrom::Start(entry.offset))?;
            if copy_hash(&mut self.file, Some(&mut output), entry.size, cancelled)? != entry.sha256
            {
                return Err(invalid("Onefile payload hash mismatch"));
            }
            output.sync_all()?;
        }
        self.verify(&staging, cancelled)?;
        check_cancel(cancelled)?;
        let source = wide(&staging);
        let target = wide(&cache);
        // SAFETY: terminated live paths; MoveFileW rejects any existing destination.
        if unsafe {
            windows_sys::Win32::Storage::FileSystem::MoveFileW(source.as_ptr(), target.as_ptr())
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok((cache, false))
    }

    fn verify(&self, root: &Path, cancelled: &AtomicBool) -> io::Result<()> {
        let mut found = BTreeSet::new();
        inventory(root, root, &mut found, &mut 0, cancelled)?;
        let expected: BTreeSet<_> = self
            .index
            .files
            .iter()
            .map(|entry| entry.path.clone())
            .collect();
        if found != expected {
            return Err(invalid(
                "Cache contains missing or unexpected files; use a fresh cache",
            ));
        }
        for entry in &self.index.files {
            let path = root.join(&entry.path);
            reject_reparse(&path)?;
            let mut file = File::open(path)?;
            if file.metadata()?.len() != entry.size
                || copy_hash(&mut file, None, entry.size, cancelled)? != entry.sha256
            {
                return Err(invalid(
                    "Cache hash mismatch; preserve it for diagnosis and use a fresh cache",
                ));
            }
        }
        Ok(())
    }
}

fn copy_hash(
    input: &mut File,
    mut output: Option<&mut File>,
    size: u64,
    cancelled: &AtomicBool,
) -> io::Result<String> {
    let mut remaining = size;
    let mut buffer = [0u8; 65536];
    let mut hash = Sha256::new();
    while remaining > 0 {
        check_cancel(cancelled)?;
        let count = remaining.min(buffer.len() as u64) as usize;
        input.read_exact(&mut buffer[..count])?;
        hash.update(&buffer[..count]);
        if let Some(file) = &mut output {
            file.write_all(&buffer[..count])?;
        }
        remaining -= count as u64;
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn inventory(
    root: &Path,
    current: &Path,
    files: &mut BTreeSet<String>,
    count: &mut usize,
    cancelled: &AtomicBool,
) -> io::Result<()> {
    check_cancel(cancelled)?;
    reject_reparse(current)?;
    *count += 1;
    if *count > 20_000
        || (current != root
            && !valid_path(
                &current
                    .strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/"),
            ))
    {
        return Err(invalid("Cache directory/path limit exceeded"));
    }
    for child in fs::read_dir(current)? {
        let child = child?.path();
        reject_reparse(&child)?;
        if child.is_dir() {
            inventory(root, &child, files, count, cancelled)?;
        } else if child.is_file() {
            if files.len() >= 10_000 {
                return Err(invalid("Cache file limit exceeded"));
            }
            files.insert(
                child
                    .strip_prefix(root)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/"),
            );
        } else {
            return Err(invalid("Cache contains a non-regular file"));
        }
    }
    Ok(())
}
fn reject_reparse(path: &Path) -> io::Result<()> {
    if fs::symlink_metadata(path)?.file_attributes() & 0x400 != 0 {
        return Err(invalid("Reparse point is not allowed in onefile cache"));
    }
    Ok(())
}
fn directory_guard(path: &Path) -> io::Result<File> {
    reject_reparse(path)?;
    if !path.is_dir() {
        return Err(invalid("Expected cache directory"));
    }
    OpenOptions::new()
        .access_mode(0x80)
        .share_mode(3)
        .custom_flags(0x02200000)
        .open(path)
}

fn private_directory(path: &Path) -> io::Result<()> {
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::{
            Authorization::{
                ConvertSecurityDescriptorToStringSecurityDescriptorW,
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW,
                SE_FILE_OBJECT,
            },
            DACL_SECURITY_INFORMATION, SECURITY_ATTRIBUTES,
        },
        Storage::FileSystem::CreateDirectoryW,
    };
    let sddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)";
    let text: Vec<_> = sddl.encode_utf16().chain([0]).collect();
    let mut descriptor = std::ptr::null_mut();
    // SAFETY: terminated SDDL, valid output pointer; descriptor is freed below.
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            text.as_ptr(),
            1,
            &mut descriptor,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    let name = wide(path);
    // SAFETY: descriptor/path live through creation; existing directory is never modified.
    let created = unsafe { CreateDirectoryW(name.as_ptr(), &attributes) };
    let error = io::Error::last_os_error();
    // SAFETY: API allocated descriptor with LocalAlloc.
    unsafe {
        LocalFree(descriptor);
    }
    if created == 0 && error.raw_os_error() != Some(183) {
        return Err(error);
    }
    reject_reparse(path)?;
    let mut descriptor = std::ptr::null_mut();
    // SAFETY: valid name/output pointer; API allocates the requested descriptor.
    let status = unsafe {
        GetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(io::Error::from_raw_os_error(status as i32));
    }
    let mut output = std::ptr::null_mut();
    // SAFETY: live descriptor; output is an allocated terminated UTF-16 string.
    let converted = unsafe {
        ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            DACL_SECURITY_INFORMATION,
            &mut output,
            std::ptr::null_mut(),
        )
    };
    let error = io::Error::last_os_error();
    // SAFETY: descriptor allocated by GetNamedSecurityInfoW.
    unsafe {
        LocalFree(descriptor);
    }
    if converted == 0 {
        return Err(error);
    }
    let mut length = 0;
    // SAFETY: successful conversion provides a terminated string; copy before LocalFree.
    let actual = unsafe {
        while *output.add(length) != 0 {
            length += 1;
        }
        let value = String::from_utf16_lossy(std::slice::from_raw_parts(output, length));
        LocalFree(output.cast());
        value
    };
    if actual != sddl {
        return Err(invalid("Cache ACL differs from the private prototype ACL"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(change: impl FnOnce(&mut Index)) -> PathBuf {
        let mut nonce = [0u8; 8];
        getrandom::fill(&mut nonce).unwrap();
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("test-results")
            .join(format!("onefile-unit-{}-{name}", std::process::id()));
        fs::create_dir(&root).unwrap();
        let mut bytes = vec![0; 512];
        bytes[..2].copy_from_slice(b"MZ");
        bytes[60..64].copy_from_slice(&64u32.to_le_bytes());
        bytes[64..68].copy_from_slice(b"PE\0\0");
        bytes[68..70].copy_from_slice(&0x8664u16.to_le_bytes());
        bytes[70..72].copy_from_slice(&1u16.to_le_bytes());
        bytes[84..86].copy_from_slice(&240u16.to_le_bytes());
        bytes[88..90].copy_from_slice(&0x20bu16.to_le_bytes());
        bytes[196..200].copy_from_slice(&16u32.to_le_bytes());
        bytes[344..348].copy_from_slice(&128u32.to_le_bytes());
        bytes[348..352].copy_from_slice(&384u32.to_le_bytes());
        let mut index = Index {
            format: 1,
            framework_version: crate::VERSION.into(),
            app_id: "com.example.unit".into(),
            host_size: 512,
            files: Vec::new(),
        };
        for path in [
            "app/pyproject.toml",
            "runtime/python/python.exe",
            "runtime/python/python314._pth",
            "runtime/webview2/msedgewebview2.exe",
            "packages/pyyaki/worker.py",
        ] {
            index.files.push(Entry {
                path: path.into(),
                offset: bytes.len() as u64,
                size: 4,
                sha256: format!("{:x}", Sha256::digest(b"data")),
            });
            bytes.extend_from_slice(b"data");
        }
        change(&mut index);
        let offset = bytes.len() as u64;
        let mut data = serde_json::to_vec(&index).unwrap();
        while (bytes.len() + data.len()) % 8 != 0 {
            data.push(b' ');
        }
        bytes.extend_from_slice(&data);
        bytes.extend_from_slice(MAGIC);
        bytes.extend_from_slice(&offset.to_le_bytes());
        bytes.extend_from_slice(&(data.len() as u64).to_le_bytes());
        bytes.extend_from_slice(&Sha256::digest(&data));
        bytes.extend_from_slice(&[0; 8]);
        let path = root.join("synthetic.exe");
        fs::write(&path, bytes).unwrap();
        path
    }
    #[test]
    fn rejects_windows_aliases_and_unsafe_components() {
        for path in [
            "../x",
            "C:/x",
            "a\\b",
            "a:b",
            "a/../b",
            "x.",
            "x ",
            "NUL.txt",
            "com1/x",
            "lPt9.dat",
            "/x",
            "a//b",
            "ui/鯛.html",
        ] {
            assert!(!valid_path(path), "{path}");
        }
        assert!(valid_path("runtime/python/python314.dll"));
    }
    #[test]
    fn rejects_ranges_collisions_missing_files_and_metadata() {
        for mode in 0..7 {
            let path = fixture(|index| match mode {
                0 => index.files[0].path = "../escape".into(),
                1 => index.files[1].path = "APP/PYPROJECT.TOML".into(),
                2 => index.files[0].size = u64::MAX,
                3 => index.files[1].offset += 1,
                4 => index.app_id = "../escape".into(),
                5 => index.files[0].path = "runtime".into(),
                _ => index.framework_version = "other".into(),
            });
            assert!(Package::open(&path).is_err(), "mode {mode}");
        }
    }
    #[test]
    fn certificate_aware_location_is_structural_not_a_signature_validation() {
        let path = fixture(|_| {});
        assert!(Package::open(&path).is_ok());
        let mut bytes = fs::read(&path).unwrap();
        let size = bytes.len() as u32;
        bytes[232..236].copy_from_slice(&size.to_le_bytes());
        bytes[236..240].copy_from_slice(&8u32.to_le_bytes());
        // Empty synthetic WIN_CERTIFICATE: deliberately NOT a real Authenticode signature.
        bytes.extend_from_slice(&[8, 0, 0, 0, 0, 2, 2, 0]);
        fs::write(&path, &bytes).unwrap();
        assert!(Package::open(&path).is_ok());
        bytes[236..240].copy_from_slice(&9u32.to_le_bytes());
        fs::write(&path, bytes).unwrap();
        assert!(Package::open(&path).is_err());
    }
    #[test]
    fn private_cache_reuse_and_corruption_fail_closed() {
        let path = fixture(|_| {});
        let base = path.parent().unwrap();
        let cancelled = AtomicBool::new(false);
        let mut package = Package::open(&path).unwrap();
        let (cache, reused) = package.prepare(base, &cancelled).unwrap();
        assert!(!reused);
        assert!(package.prepare(base, &cancelled).unwrap().1);
        fs::write(cache.join("runtime/python/python.exe"), b"evil").unwrap();
        assert!(
            package
                .prepare(base, &cancelled)
                .err()
                .unwrap()
                .to_string()
                .contains("Cache hash mismatch")
        );
        fs::write(cache.join("runtime/python/python.exe"), b"data").unwrap();
        fs::write(cache.join("unexpected.pyc"), b"x").unwrap();
        assert!(
            package
                .prepare(base, &cancelled)
                .err()
                .unwrap()
                .to_string()
                .contains("unexpected")
        );
    }
    #[test]
    fn corrupt_payload_index_and_cancel_never_publish_cache() {
        let path = fixture(|_| {});
        let base = path.parent().unwrap();
        let cancelled = AtomicBool::new(false);
        let mut bytes = fs::read(&path).unwrap();
        bytes[512] ^= 1;
        fs::write(&path, &bytes).unwrap();
        let mut package = Package::open(&path).unwrap();
        assert!(
            package
                .prepare(base, &cancelled)
                .err()
                .unwrap()
                .to_string()
                .contains("payload hash")
        );
        assert!(
            !base
                .join("PyYakiOnefile/com.example.unit")
                .join(&package.id)
                .exists()
        );
        bytes[512] ^= 1;
        fs::write(&path, &bytes).unwrap();
        cancelled.store(true, Ordering::Release);
        assert!(
            Package::open(&path)
                .unwrap()
                .prepare(base, &cancelled)
                .is_err()
        );
        let length = bytes.len();
        bytes[length - 40] ^= 1;
        fs::write(&path, bytes).unwrap();
        assert!(Package::open(&path).is_err());
    }
}
