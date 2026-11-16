from app.services.auth.firebase_auth.auth_errors import AuthError


class SmsSendError(AuthError):
    """Base class for SMS delivery failures - converted to an HTTP response by
    the AuthError handler when it escapes a request (e.g. an inline OTP send)."""
    status_code = 502
    detail = "We couldn't send the SMS"


class SmsServiceUnavailableError(SmsSendError):
    """Transient - timeout, network error, 5xx or an AT gateway error. The
    only SMS error worth retrying."""
    status_code = 503
    detail = "The SMS service is temporarily unavailable, please try again"


class InvalidSmsRecipientError(SmsSendError):
    """The number can't receive SMS (invalid, unsupported, or blacklisted)."""
    status_code = 400
    detail = "This phone number can't receive SMS"


class SmsRejectedError(SmsSendError):
    """Africa's Talking refused the message because of our account or config
    (risk hold, bad sender ID, no balance, no route) - needs a human to fix."""
    status_code = 502
    detail = "We couldn't send the SMS"
