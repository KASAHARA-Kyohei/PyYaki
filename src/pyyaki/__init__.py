"""PyYaki's initial web prototype. Windows support is not implemented."""

from .app import App
from .errors import AppError, FrameworkError

__version__ = "0.1.0.dev0"
__all__ = ["App", "AppError", "FrameworkError"]
