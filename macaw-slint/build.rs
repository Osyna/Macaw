fn main() {
    slint_build::compile("ui/app.slint").expect("slint compile failed");
    // Explorer/taskbar icon for macaw-ui.exe. Host-gated: cross-builds from
    // Linux skip it (the block needs a Windows resource compiler anyway).
    #[cfg(windows)]
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let _ = winresource::WindowsResource::new()
            .set_icon("../packaging/windows/icon.ico")
            .compile();
    }
}
