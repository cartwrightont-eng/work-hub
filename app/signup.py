import re, secrets
from flask import Blueprint, jsonify, request, render_template, g, redirect
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from flask_jwt_extended import create_access_token, set_access_cookies
from .db import db_cursor
from .auth import limiter, login_required
from .admin_service import create_user, add_member, set_roles

bp = Blueprint("signup", __name__)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I, so codes are easy to read out

def _norm(code): return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
def _show(code): return code[:4] + "-" + code[4:]

def _can_see_code(cur, user_id, org):
    cur.execute("""SELECT 1 FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id
                   JOIN permissions p ON p.id=rp.permission_id
                   WHERE ur.user_id=%s AND ur.org_id=%s AND p.key='manage_users'""", (user_id, org))
    return cur.fetchone() is not None

def _new_code(cur, org):
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    cur.execute("INSERT INTO org_join_codes (org_id, code) VALUES (%s,%s) "
                "ON CONFLICT (org_id) DO UPDATE SET code=EXCLUDED.code, created_at=now()", (org, code))
    return code

def _find_org(cur, org_name, code):
    """Both the name and the code must match; the caller gets one generic error either way."""
    cur.execute("""SELECT o.id FROM organisations o JOIN org_join_codes c ON c.org_id=o.id
                   WHERE c.code=%s AND lower(o.name)=lower(%s)""", (_norm(code), str(org_name or "").strip()))
    r = cur.fetchone()
    return r["id"] if r else None

@bp.get("/signup")
def page():
    return render_template("signup.html")

@bp.post("/auth/signup/lookup")
@limiter.limit("10 per minute")
def lookup():
    d = request.get_json(silent=True) or {}
    with db_cursor(commit=False) as cur:
        org = _find_org(cur, d.get("org"), d.get("code"))
        if not org: return jsonify(error="organisation name or code is wrong"), 403
        cur.execute("SELECT id, name FROM teams WHERE org_id=%s ORDER BY name", (org,))
        return jsonify(teams=cur.fetchall())

@bp.post("/auth/signup")
@limiter.limit("10 per hour")
def submit():
    d = request.get_json(silent=True) or {}
    name = str(d.get("full_name") or "").strip()
    email = str(d.get("email") or "").strip().lower()
    pw = str(d.get("password") or "")
    if not 2 <= len(name) <= 80: return jsonify(error="enter your full name"), 400
    if len(email) > 200 or not EMAIL.match(email): return jsonify(error="enter a valid email"), 400
    if not 10 <= len(pw) <= 200: return jsonify(error="password must be at least 10 characters"), 400
    try: team = int(d.get("team_id"))
    except (TypeError, ValueError): return jsonify(error="choose a team"), 400
    with db_cursor() as cur:
        org = _find_org(cur, d.get("org"), d.get("code"))
        if not org: return jsonify(error="organisation name or code is wrong"), 403
        cur.execute("SELECT 1 FROM teams WHERE id=%s AND org_id=%s", (team, org))
        if not cur.fetchone(): return jsonify(error="choose a team"), 400
        cur.execute("SELECT 1 FROM users WHERE email=%s", (email,))
        if cur.fetchone(): return jsonify(error="an account with this email already exists, sign in instead"), 409
        cur.execute("SELECT user_id FROM team_memberships WHERE team_id=%s AND is_lead LIMIT 1", (team,))
        lead = cur.fetchone()
        uid = create_user(cur, org, name, email, pw)
        add_member(cur, org, team, uid, reports_to=lead["user_id"] if lead else None)
        set_roles(cur, org, uid, ["member"])
        cur.execute("INSERT INTO audit_log (org_id, actor_id, action, detail) VALUES (%s,%s,'self_signup',%s)",
                    (org, uid, '{"team_id": %d}' % team))
    resp = jsonify(ok=True)
    set_access_cookies(resp, create_access_token(identity=str(uid)))
    return resp

@bp.get("/api/join-code")
@login_required
def get_code():
    with db_cursor() as cur:
        if not _can_see_code(cur, g.user["id"], g.user["org_id"]): return jsonify(error="forbidden"), 403
        cur.execute("SELECT code FROM org_join_codes WHERE org_id=%s", (g.user["org_id"],))
        row = cur.fetchone()
        code = row["code"] if row else _new_code(cur, g.user["org_id"])
        cur.execute("SELECT name FROM organisations WHERE id=%s", (g.user["org_id"],))
        return jsonify(code=_show(code), org=cur.fetchone()["name"])

@bp.post("/api/join-code/regenerate")
@login_required
def regenerate():
    with db_cursor() as cur:
        if not _can_see_code(cur, g.user["id"], g.user["org_id"]): return jsonify(error="forbidden"), 403
        code = _new_code(cur, g.user["org_id"])
        cur.execute("INSERT INTO audit_log (org_id, actor_id, action, detail) VALUES (%s,%s,'join_code_regenerated','{}')",
                    (g.user["org_id"], g.user["id"]))
        return jsonify(code=_show(code))


@bp.get("/join-code")
def join_code_page():
    from .overview import _nav
    try:
        verify_jwt_in_request()
        me = int(get_jwt_identity())
    except Exception:
        return redirect("/login")
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT org_id FROM users WHERE id=%s AND active", (me,))
        u = cur.fetchone()
        if not u: return redirect("/login")
        if not _can_see_code(cur, me, u["org_id"]): return render_template("notfound.html"), 404
        return render_template("join_code.html", nav=_nav(cur, me, u["org_id"]))
