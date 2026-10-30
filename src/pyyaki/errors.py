"""Errors that are safe to send over the public command protocol."""


class FrameworkError(Exception):
    def __init__(self, code: str, message: str, details: object = None):
        if not isinstance(code, str) or not isinstance(message, str):
            raise TypeError("Error codes and messages must be strings")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def payload(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.details is not None:
            result["details"] = self.details
        return result


class AppError(FrameworkError):
    def __init__(self, code: str, message: str, details: object = None):
        if not isinstance(code, str) or not code.startswith("APP_"):
            raise ValueError("AppError codes must start with APP_")
        super().__init__(code, message, details)
