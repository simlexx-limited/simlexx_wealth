import tempfile
from pathlib import Path

# The app's WORKDIR (/app) is root-owned in the container while the process
# runs as a non-root user, so a relative path here is not writable there.
# The OS temp dir is writable by any user in any environment (host or
# container) - retrieve with `docker exec simlexx_api cat /tmp/otp_code.txt`.
OTP_CODE_FILE = Path(tempfile.gettempdir()) / "otp_code.txt"


async def send_otp_sms(phone_number: str, code: str) -> None:
    """
    Placeholder SMS delivery until a dedicated SMS provider is wired in -
    prints the OTP and appends it to otp_code.txt so it can be shared with
    the team for testing. Swap the body of this function out once an SMS
    service is chosen; callers don't need to change.
    """
    line = f"OTP for {phone_number}: {code}"
    print(line)
    with OTP_CODE_FILE.open("a") as f:
        f.write(line + "\n")
