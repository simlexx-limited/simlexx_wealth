import secrets

def generate_otp_code() -> int:
    """
    generate random otp code
    """
    return 10000000 + secrets.randbelow(90000000)
