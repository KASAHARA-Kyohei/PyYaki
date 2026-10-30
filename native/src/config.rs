use serde::Deserialize;
use std::{
    error::Error,
    fs,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
struct Root {
    tool: Tools,
}
#[derive(Deserialize)]
struct Tools {
    pyyaki: Settings,
}
#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
struct Settings {
    #[serde(default = "default_ui")]
    ui_dir: String,
    #[serde(default = "default_entry")]
    ui_entry: String,
    #[serde(default)]
    window: Window,
}
#[derive(Deserialize)]
pub struct Window {
    #[serde(default = "default_title")]
    pub title: String,
    #[serde(default = "default_width")]
    pub width: u32,
    #[serde(default = "default_height")]
    pub height: u32,
}
fn default_ui() -> String {
    "ui".into()
}
fn default_entry() -> String {
    "index.html".into()
}
fn default_title() -> String {
    "PyYaki".into()
}
fn default_width() -> u32 {
    960
}
fn default_height() -> u32 {
    720
}
impl Default for Window {
    fn default() -> Self {
        Self {
            title: default_title(),
            width: default_width(),
            height: default_height(),
        }
    }
}

pub struct Config {
    pub root: PathBuf,
    pub ui_dir: PathBuf,
    pub ui_entry: String,
    pub window: Window,
}

impl Config {
    pub fn load(root: &Path) -> Result<Self, Box<dyn Error>> {
        let root = root.canonicalize()?;
        let settings = toml::from_str::<Root>(&fs::read_to_string(root.join("pyproject.toml"))?)?
            .tool
            .pyyaki;
        if Path::new(&settings.ui_dir).is_absolute() || Path::new(&settings.ui_entry).is_absolute()
        {
            return Err("UI settings must use relative paths".into());
        }
        let ui_dir = root.join(&settings.ui_dir).canonicalize()?;
        let page = ui_dir.join(&settings.ui_entry).canonicalize()?;
        if !ui_dir.starts_with(&root) || !page.starts_with(&ui_dir) || !page.is_file() {
            return Err("UI entry must stay inside the project UI directory".into());
        }
        if !(320..=4096).contains(&settings.window.width)
            || !(240..=4096).contains(&settings.window.height)
        {
            return Err("Window size is outside prototype limits".into());
        }
        Ok(Self {
            root,
            ui_dir,
            ui_entry: settings.ui_entry,
            window: settings.window,
        })
    }
}
