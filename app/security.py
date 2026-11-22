import re
from werkzeug.security import generate_password_hash, check_password_hash

def password_problems(pw):
    p = []
    if len(pw or "") < 12: p.append("at least 12 characters")
    if not re.search(r"[a-z]", pw or ""): p.append("a lowercase letter")
    if not re.search(r"[A-Z]", pw or ""): p.append("an uppercase letter")
    if not re.search(r"\d", pw or ""): p.append("a digit")
    return p

def hash_password(pw):
    bad = password_problems(pw)
    if bad: raise ValueError("Password needs " + ", ".join(bad))
    return generate_password_hash(pw)

verify_password = check_password_hash
