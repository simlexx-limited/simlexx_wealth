import secrets
import string

SPECIALS = "!@#$%^&*-_=+?"
ALPHABET = string.ascii_letters + string.digits + SPECIALS


def generate_temporary_password(length: int = 16) -> str:
    """
    A random password that always satisfies the password rules (upper case,
    lower case, digit and special character), for accounts an administrator
    creates on somebody's behalf. The person must change it at first login.
    """
    if length < 12:
        raise ValueError("A temporary password must be at least 12 characters long")
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(SPECIALS),
    ]
    rest = [secrets.choice(ALPHABET) for _ in range(length - len(required))]
    characters = required + rest
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)
