import bcrypt

def generate_hash(password:str, rounds:int=12) -> str:
    """
    generate password hash
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()

def compare_hash(password:str, save_password:str) -> bool:
    """
    compare hash. return bool"""
    return bcrypt.checkpw(password.encode(), save_password.encode())
