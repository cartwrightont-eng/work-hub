from functools import wraps
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import (create_access_token, jwt_required, get_jwt_identity,
                                set_access_cookies, unset_jwt_cookies)
from .db import db_cursor
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from .security import verify_password

limiter = Limiter(get_remote_address, storage_uri="memory://")

bp = Blueprint("auth", __name__)

def login_required(fn):
    """JWT carries only the user id. Org and active state are read fresh each request."""
    @wraps(fn)
    @jwt_required()
    def wrapper(*a, **kw):
        with db_cursor(commit=False) as cur:
            cur.execute("SELECT id, org_id, full_name FROM users WHERE id=%s AND active",
                        (int(get_jwt_identity()),))
            user = cur.fetchone()
        if not user:
            return jsonify(error="unauthorised"), 401
        g.user = user
        return fn(*a, **kw)
    return wrapper

@bp.post("/auth/login")
@limiter.limit("5 per minute")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT id, password_hash FROM users WHERE email=%s AND active", (email,))
        u = cur.fetchone()
    if not u or not u["password_hash"] or not verify_password(u["password_hash"], data.get("password") or ""):
        return jsonify(error="invalid credentials"), 401
    resp = jsonify(ok=True)
    set_access_cookies(resp, create_access_token(identity=str(u["id"])))
    return resp

@bp.post("/auth/logout")
@jwt_required()
def logout():
    resp = jsonify(ok=True)
    unset_jwt_cookies(resp)
    return resp

@bp.get("/auth/me")
@login_required
def me():
    return jsonify(id=g.user["id"], name=g.user["full_name"])
