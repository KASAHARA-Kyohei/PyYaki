use percent_encoding::percent_decode_str;
use std::{
    fs, io,
    path::{Component, Path},
};

pub const SDK: &str = include_str!("../../src/pyyaki/static/sdk.js");
pub const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

pub fn resolve(root: &Path, entry: &str, path: &str) -> io::Result<(Vec<u8>, &'static str)> {
    let decoded = percent_decode_str(path)
        .decode_utf8()
        .map_err(io::Error::other)?;
    if !decoded.starts_with('/') || decoded.contains(['\\', ':', '\0', '%']) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "invalid asset path",
        ));
    }
    let relative = decoded.trim_start_matches('/');
    if relative == "_pyyaki/sdk.js" {
        return Ok((SDK.as_bytes().to_vec(), "text/javascript; charset=utf-8"));
    }
    if relative.starts_with("_pyyaki/") || relative.split('/').any(|p| p.starts_with('.')) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "reserved or private asset",
        ));
    }
    let relative = if relative.is_empty() { entry } else { relative };
    if Path::new(relative)
        .components()
        .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "non-relative asset",
        ));
    }
    let root = root.canonicalize()?;
    let file = root.join(relative).canonicalize()?;
    if !file.starts_with(root) || !file.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "asset escaped UI root",
        ));
    }
    let mime = match file.extension().and_then(|s| s.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "json" => "application/json",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "svg" => "image/svg+xml",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        _ => {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "unsupported asset type",
            ));
        }
    };
    if fs::metadata(&file)?.len() > 16 * 1024 * 1024 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "asset exceeds prototype 16 MiB cap",
        ));
    }
    Ok((fs::read(file)?, mime))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_traversal_and_reserved_paths() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/hello/ui");
        for path in [
            "/../app.py",
            "/%2e%2e/app.py",
            "/%252e%252e/x",
            "/.env",
            "/C:/x",
            "/a%5cb",
            "/_pyyaki/secret",
        ] {
            assert!(resolve(&root, "index.html", path).is_err(), "{path}");
        }
        assert!(
            resolve(&root, "index.html", "/")
                .unwrap()
                .1
                .starts_with("text/html")
        );
        assert!(
            resolve(&root, "index.html", "/_pyyaki/sdk.js")
                .unwrap()
                .0
                .len()
                > 100
        );
    }
}
