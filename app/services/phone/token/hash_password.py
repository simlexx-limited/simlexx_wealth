import bcrypt

def generate_hash(password:str, rounds:int=12) -> str:
    """
    generate password hash
    """
    return str(bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)))
