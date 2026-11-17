import tempfile
from pathlib import Path

from app.services.sms.sms_result import SmsResult

# The app's WORKDIR (/app) is root-owned in the container while the process
# runs as a non-root user, so a relative path here is not writable there.
# The OS temp dir is writable by any user in any environment (host or
# container) - retrieve with `docker exec simlexx_api cat /tmp/otp_code.txt`.
OTP_CODE_FILE = Path(tempfile.gettempdir()) / "otp_code.txt"


async def send_via_console(to: str, message: str) -> SmsResult:
    """
    SMS_PROVIDER=console (dev/CI): prints the message and appends it to
    otp_code.txt so codes can be shared with the team for testing.
    """
    line = f"SMS to {to}: {message}"
    print(line)
    with OTP_CODE_FILE.open("a") as f:
        f.write(line + "\n")
    return SmsResult(provider="console", status="Logged")
