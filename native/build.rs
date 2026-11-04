fn main() {
    println!("cargo:rerun-if-changed=icons/pyyaki.rc");
    println!("cargo:rerun-if-changed=icons/pyyaki.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        // The window loads ordinal 1 too; never silently ship a host without it.
        embed_resource::compile("icons/pyyaki.rc", embed_resource::NONE)
            .manifest_required()
            .expect("Compile the PyYaki icon with the Windows SDK resource compiler or windres");
    }
}
