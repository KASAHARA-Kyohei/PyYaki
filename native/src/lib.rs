pub mod assets;
pub mod command_line;
pub mod config;
pub mod framing;
pub mod navigation;

pub const VERSION: &str = "0.1.0.dev0";
pub const MAX_MESSAGE_BYTES: usize = 1_048_576;
pub const ORIGIN: &str = "https://pyyaki.localhost";
