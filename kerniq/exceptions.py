"""Custom exceptions for Kerniq."""

class KerniqError(Exception):
    """Base exception for all Kerniq errors."""
    pass

class ConfigError(KerniqError):
    """Raised when configuration is invalid, missing, or cannot be loaded."""
    pass

class StorageError(KerniqError):
    """Raised when local database or output directory operations fail."""
    pass

class ProcessError(KerniqError):
    """Raised when launching, monitoring, or terminating a process fails."""
    pass

class TelegramError(KerniqError):
    """Raised when communication with Telegram Bot API fails."""
    def __init__(self, message: str, status_code: int = None, retry_after: int = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after

class AuthorizationError(KerniqError):
    """Raised when an unauthorized user attempts an operation or permission is insufficient."""
    pass

class TaskNotFoundError(KerniqError):
    """Raised when a task with the given ID cannot be found."""
    pass
