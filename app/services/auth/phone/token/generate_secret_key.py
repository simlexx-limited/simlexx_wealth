import secrets

def secret_keys() -> str:
    return secrets.token_hex(4)
