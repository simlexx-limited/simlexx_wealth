class EmailSendError(Exception):
    """Base exception for email sending failures."""


class InvalidRecipientError(EmailSendError):
    """Raised when the SMTP server rejects the recipient address."""


class EmailServiceUnavailableError(EmailSendError):
    """Raised when the SMTP server can't be reached or credentials are rejected."""
