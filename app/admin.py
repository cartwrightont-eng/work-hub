import psycopg2
from flask import Blueprint, jsonify, request, g, render_template
from .auth import login_required
from .db import db_cursor
from .deliverables import err
from .overview import page_login, _nav
from .scope import has_permission
from . import admin_service as svc

bp = Blueprint("admin", __name__)

def guard(cur, perm):
    """Non-admins get 404, so the existence of admin screens is not advertised."""
    return has_permission(cur, g.user["id"], g.user["org_id"], perm)

def _run(perm, fn):
    org = g.user["org_id"]
    try:
        with db_cursor() as cur:
            if not guard(cur, perm): return err("not found", 404)
            return fn(cur, org, request.get_json(silent=True) or {})
    except ValueError as e: return err(str(e), 422)
    except psycopg2.IntegrityError: return err("that name or email already exists, or a reference is invalid", 422)

def _owned(cur, table, org, id_):
    cur.execute(f"SELECT id FROM {table} WHERE id=%s AND org_id=%s", (id_, org))   # table names are fixed literals below
    return cur.fetchone() is not None

@bp.get("/api/settings")
@login_required
def get_settings():
    return _run("manage_settings", lambda cur, org, j: jsonify(svc.effective_settings(cur, org)))

@bp.put("/api/settings")
@login_required
def put_settings():
    def go(cur, org, j):
        svc.save_settings(cur, org, j, actor=g.user["id"])
        return jsonify(svc.effective_settings(cur, org))
    return _run("manage_settings", go)

@bp.get("/api/sizing")
@login_required
def sizing():
    with db_cursor(commit=False) as cur:
        s = svc.effective_settings(cur, g.user["org_id"])
    return jsonify(scale=s["point_scale"], labels=s["point_labels"], rubric=s["point_rubric"])

@bp.post("/api/admin/teams")
@login_required
def new_team():
    def go(cur, org, j):
        name = (j.get("name") or "").strip()
        if not name: raise ValueError("a team name is required")
        return jsonify(id=svc.create_team(cur, org, name)), 201
    return _run("manage_teams", go)

@bp.post("/api/admin/users")
@login_required
def new_user():
    def go(cur, org, j):
        name = (j.get("full_name") or "").strip()
        if not name: raise ValueError("full_name is required")
        uid = svc.create_user(cur, org, name, j.get("email"), j.get("password"))
        m = j.get("membership")
        if m:
            _check_membership(cur, org, uid, m)
            svc.add_member(cur, org, m["team_id"], uid, bool(m.get("is_lead")), m.get("reports_to"))
        svc.set_roles(cur, org, uid, j.get("roles") or ["lead" if m and m.get("is_lead") else "member"])
        return jsonify(id=uid), 201
    return _run("manage_users", go)

def _check_membership(cur, org, uid, m):
    if not _owned(cur, "teams", org, m.get("team_id")): raise ValueError("unknown team")
    r = m.get("reports_to")
    if r is not None and (r == uid or not _owned(cur, "users", org, r)): raise ValueError("invalid reporting line")

@bp.patch("/api/admin/users/<int:uid>")
@login_required
def edit_user(uid):
    def go(cur, org, j):
        if not _owned(cur, "users", org, uid): return err("not found", 404)
        if "email" in j: cur.execute("UPDATE users SET email=%s WHERE id=%s", ((j["email"] or "").strip().lower() or None, uid))
        if j.get("password"): cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (svc.hash_password(j["password"]), uid))
        if "active" in j: cur.execute("UPDATE users SET active=%s WHERE id=%s", (bool(j["active"]), uid))
        m = j.get("membership")
        if m:
            _check_membership(cur, org, uid, m)
            svc.add_member(cur, org, m["team_id"], uid, bool(m.get("is_lead")), m.get("reports_to"))
        if j.get("leave_team_id"):
            cur.execute("DELETE FROM team_memberships WHERE org_id=%s AND user_id=%s AND team_id=%s", (org, uid, j["leave_team_id"]))
        if "roles" in j: svc.set_roles(cur, org, uid, j["roles"])
        return jsonify(id=uid)
    return _run("manage_users", go)

@bp.get("/api/admin/roles")
@login_required
def roles():
    def go(cur, org, j):
        cur.execute("""SELECT r.id, r.name, COALESCE(array_agg(p.key ORDER BY p.key) FILTER (WHERE p.key IS NOT NULL), '{}') AS permissions
                       FROM roles r LEFT JOIN role_permissions rp ON rp.role_id=r.id LEFT JOIN permissions p ON p.id=rp.permission_id
                       WHERE r.org_id=%s GROUP BY r.id ORDER BY r.name""", (org,))
        return jsonify(roles=cur.fetchall(), all_permissions=svc.PERMISSIONS)
    return _run("manage_users", go)

@bp.post("/api/admin/roles")
@login_required
def new_role():
    def go(cur, org, j):
        name = (j.get("name") or "").strip()
        if not name: raise ValueError("a role name is required")
        return jsonify(id=svc.create_role(cur, org, name, j.get("permissions") or [])), 201
    return _run("manage_users", go)

@bp.put("/api/admin/roles/<int:rid>")
@login_required
def edit_role(rid):
    def go(cur, org, j):
        if not _owned(cur, "roles", org, rid): return err("not found", 404)
        perms = j.get("permissions")
        if not isinstance(perms, list) or set(perms) - set(svc.PERMISSIONS): raise ValueError("unknown permission")
        svc.set_role_permissions(cur, org, rid, perms)
        return jsonify(id=rid, permissions=sorted(perms))
    return _run("manage_users", go)

@bp.get("/settings")
@page_login
def settings_page():
    with db_cursor(commit=False) as cur:
        if not guard(cur, "manage_settings"): return render_template("notfound.html"), 404
        return render_template("settings.html", s=svc.effective_settings(cur, g.user["org_id"]), nav=_nav(cur, g.user["id"], g.user["org_id"]))

@bp.get("/api/admin/people")
@login_required
def people_admin():
    def go(cur, org, j):
        return jsonify(_people_payload(cur, org))
    return _run("manage_users", go)

def _people_payload(cur, org):
    cur.execute("SELECT id, full_name AS name, email, active, password_hash IS NOT NULL AS has_password FROM users WHERE org_id=%s ORDER BY full_name", (org,))
    people = cur.fetchall()
    cur.execute("""SELECT m.user_id, t.id AS team_id, t.name AS team, m.is_lead, r.full_name AS reports_to FROM team_memberships m
                   JOIN teams t ON t.id=m.team_id LEFT JOIN users r ON r.id=m.reports_to WHERE m.org_id=%s ORDER BY t.name""", (org,))
    mem = cur.fetchall()
    cur.execute("SELECT ur.user_id, r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.org_id=%s ORDER BY r.name", (org,))
    roles = cur.fetchall()
    for p in people:
        p["teams"] = [{k: v for k, v in m.items() if k != "user_id"} for m in mem if m["user_id"] == p["id"]]
        p["roles"] = [r["name"] for r in roles if r["user_id"] == p["id"]]
    cur.execute("SELECT id, name FROM teams WHERE org_id=%s ORDER BY name", (org,)); teams = cur.fetchall()
    cur.execute("SELECT name FROM roles WHERE org_id=%s ORDER BY name", (org,))
    return {"people": people, "teams": teams, "roles": [r["name"] for r in cur.fetchall()]}

@bp.get("/admin/people")
@page_login
def people_page():
    with db_cursor(commit=False) as cur:
        if not guard(cur, "manage_users"): return render_template("notfound.html"), 404
        return render_template("people.html", d=_people_payload(cur, g.user["org_id"]), nav=_nav(cur, g.user["id"], g.user["org_id"]))
