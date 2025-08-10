from datetime import datetime, timedelta
from jose import jwt

SECRET_KEY = "super-secret"
ALGORITHM = "HS256"

def create_token(username):
    expire = datetime.utcnow() + timedelta(minutes=1)  # ⏰ 30-minute expiry
    to_encode = {"sub": username, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
