import os, re, hmac
from flask import Blueprint, jsonify, request, render_template
from .db import db_cursor
from .auth import limiter
from .security import hash_password

bp = Blueprint("signup", __name__)
ROLES = ("member", "lead")          # CEO and admin are only ever granted by an approver
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _org(cur):
    cur.execute("SELECT id FROM organisations WHERE slug=%s", (os.environ.get("SIGNUP_ORG_SLUG", ""),))
    r = cur.fetchone()
    return r["id"] if r else None

@bp.get("/signup")
def page():
    teams = []
    with db_cursor(commit=False) as cur:
        org = _org(cur)
        if org:
            cur.execute("SELECT id, name FROM teams WHERE org_id=%s ORDER BY name", (org,))
            teams = cur.fetchall()
    return render_template("signup.html", teams=teams, roles=ROLES)

@bp.post("/auth/signup")
@limiter.limit("5 per hour")
def submit():
    code = os.environ.get("SIGNUP_CODE", "")
    d = request.get_json(silent=True) or {}
    if not code: return jsonify(error="signup is closed"), 503
    if not hmac.compare_digest(code.encode(), str(d.get("code") or "").encode()):
        return jsonify(error="wrong team code"), 403
    name = str(d.get("full_name") or "").strip()
    email = str(d.get("email") or "").strip().lower()
    pw, role = str(d.get("password") or ""), d.get("role")
    if not 2 <= len(name) <= 80: return jsonify(error="enter your full name"), 400
    if len(email) > 200 or not EMAIL.match(email): return jsonify(error="enter a valid email"), 400
    if not 10 <= len(pw) <= 200: return jsonify(error="password must be at least 10 characters"), 400
    if role not in ROLES: return jsonify(error="choose a role"), 400
    try: team = int(d.get("team_id"))
    except (TypeError, ValueError): return jsonify(error="choose a team"), 400
    with db_cursor() as cur:
        org = _org(cur)
        if not org: return jsonify(error="signup is not set up"), 503
        cur.execute("SELECT 1 FROM teams WHERE id=%s AND org_id=%s", (team, org))
        if not cur.fetchone(): return jsonify(error="choose a team"), 400
        cur.execute("SELECT 1 FROM users WHERE email=%s UNION SELECT 1 FROM signup_requests WHERE email=%s AND status='pending'", (email, email))
        if not cur.fetchone():       # same answer either way, so the form cannot be used to probe who has an account
            cur.execute("INSERT INTO signup_requests (org_id, full_name, email, password_hash, team_id, requested_role) VALUES (%s,%s,%s,%s,%s,%s)",
                        (org, name, email, hash_password(pw), team, role))
    return jsonify(ok=True)
