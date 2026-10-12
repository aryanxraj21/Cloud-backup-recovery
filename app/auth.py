"""JWT authentication + role-based access control (RBAC).

Roles:
  viewer   - read records, backups, job status
  operator - + create backups, verify, start restores
  admin    - + inject failures (chaos), read logs
"""
import os
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-in-production-32b")
ALGO = "HS256"
TTL = int(os.getenv("JWT_TTL_SECONDS", "3600"))
bearer = HTTPBearer(auto_error=False)


def issue_token(username, role):
    now = int(time.time())
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + TTL}, SECRET, algorithm=ALGO)


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(401, "missing bearer token")
    try:
        data = jwt.decode(creds.credentials, SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")
    return {"username": data["sub"], "role": data["role"]}


RANK = {"viewer": 1, "operator": 2, "admin": 3}


def require(role):
    def dep(user=Depends(current_user)):
        if RANK[user["role"]] < RANK[role]:
            raise HTTPException(403, f"requires role '{role}'")
        return user
    return dep


def login(username, password):
    u = db.query("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not u or not db.verify_password(password, u["password_hash"]):
        return None
    return issue_token(u["username"], u["role"]), u["role"]
