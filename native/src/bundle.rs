//! Fixed, relocatable layout for locally built macOS application bundles.
use serde::Deserialize;
use std::{
    error::Error,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Marker {
    format: u32,
    framework_version: String,
}

pub fn paths(executable: &Path) -> Result<(PathBuf, PathBuf), Box<dyn Error>> {
    let macos = executable.parent().ok_or("Missing executable directory")?;
    let contents = macos.parent().ok_or("Missing bundle Contents")?;
    if macos.file_name().is_none_or(|s| s != "MacOS")
        || contents.file_name().is_none_or(|s| s != "Contents")
    {
        return Err(
            "Use run --project <project> --python <venv-python> outside an app bundle".into(),
        );
    }
    let resources = contents.join("Resources").canonicalize()?;
    if !resources.starts_with(contents.canonicalize()?) {
        return Err("App Resources escape Contents".into());
    }
    let mut data = Vec::with_capacity(4097);
    fs::File::open(resources.join("pyyaki-bundle.json"))?
        .take(4097)
        .read_to_end(&mut data)?;
    if data.len() > 4096 {
        return Err("Invalid app bundle marker size".into());
    }
    let marker: Marker = serde_json::from_slice(&data)?;
    if marker.format != 1 || marker.framework_version != pyyaki_host::VERSION {
        return Err("App bundle and host version mismatch".into());
    }
    let project = resources.join("app").canonicalize()?;
    let python = resources.join("python/bin/python3");
    if !project.starts_with(&resources) || !python.canonicalize()?.starts_with(&resources) {
        return Err("App bundle paths escape Resources".into());
    }
    Ok((project, python))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::atomic::{AtomicUsize, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_FIXTURE: AtomicUsize = AtomicUsize::new(0);

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "pyyaki-bundle-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn packaged_paths_are_relative_and_version_checked() {
        let temp = Fixture::new();
        let contents = temp.0.join("Hello.app/Contents");
        let resources = contents.join("Resources");
        fs::create_dir_all(contents.join("MacOS")).unwrap();
        fs::create_dir_all(resources.join("app")).unwrap();
        fs::create_dir_all(resources.join("python/bin")).unwrap();
        fs::write(resources.join("python/bin/python3"), "").unwrap();
        let marker = resources.join("pyyaki-bundle.json");
        fs::write(
            &marker,
            format!(
                r#"{{"format":1,"framework_version":"{}"}}"#,
                pyyaki_host::VERSION
            ),
        )
        .unwrap();
        let executable = contents.join("MacOS/PyYaki");
        let (project, python) = paths(&executable).unwrap();
        assert_eq!(project, resources.join("app").canonicalize().unwrap());
        assert!(python.ends_with("Resources/python/bin/python3"));
        fs::write(&marker, r#"{"format":1,"framework_version":"wrong"}"#).unwrap();
        assert!(paths(&executable).is_err());
        fs::write(&marker, vec![b' '; 64 * 1024]).unwrap();
        assert!(paths(&executable).unwrap_err().to_string().contains("size"));
        assert!(paths(&temp.0.join("PyYaki")).is_err());
    }

    #[test]
    fn resources_cannot_escape_contents() {
        let temp = Fixture::new();
        let contents = temp.0.join("Hello.app/Contents");
        fs::create_dir_all(contents.join("MacOS")).unwrap();
        std::os::unix::fs::symlink(&temp.0, contents.join("Resources")).unwrap();
        assert!(paths(&contents.join("MacOS/PyYaki")).is_err());
    }
}
