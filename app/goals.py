import json
from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, jsonify, request, g, render_template
from .auth import login_required
from .db import db_cursor
from .deliverables import load as load_deliverable, is_manager, err, ser
from .goals_calc import compute
from .metrics import org_today
from .overview import page_login, _nav
from .scope import has_permission, can_view_objective, can_edit_objective

bp = Blueprint("goals", __name__)

def _f(v): return float(v) if isinstance(v, Decimal) else (v.isoformat() if hasattr(v, "isoformat") else v)
def _row(r): return {k: _f(v) for k, v in r.items()}

def _quarter(d):
    s = date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    nxt = date(s.year + (s.month == 10), (s.month + 2) % 12 + 1, 1)
    return s, nxt - timedelta(days=1)

def _tree(cur, org, as_of):
    cur.execute("SELECT * FROM objectives WHERE org_id=%s ORDER BY level, id", (org,)); objs = cur.fetchall()
    cur.execute("SELECT * FROM key_results WHERE org_id=%s ORDER BY id", (org,)); krs = cur.fetchall()
    return objs, krs, compute(objs, krs, as_of)

def _shape(o, krs, calc):
    from .goals_calc import kr_progress
    return {**_row(o), **calc[o["id"]], "key_results": [
        {**_row(k), "progress": round(kr_progress(k["start_value"], k["target"], k["current"]), 4)}
        for k in krs if k["objective_id"] == o["id"]]}

def _asof(cur, org):
    try: return date.fromisoformat(request.args["as_of"])
    except (KeyError, ValueError): return org_today(cur, org)

@bp.post("/api/objectives")
@login_required
def create_objective():
    me, org = g.user["id"], g.user["org_id"]
    j = request.get_json(silent=True) or {}
    level, title = j.get("level"), (j.get("title") or "").strip()
    if level not in (1, 2, 3) or not title: return err("level (1-3) and title are required", 422)
    with db_cursor() as cur:
        try:
            qs, qe = _quarter(org_today(cur, org))
            start = date.fromisoformat(j["cycle_start"]) if j.get("cycle_start") else qs
            end = date.fromisoformat(j["cycle_end"]) if j.get("cycle_end") else qe
        except ValueError: return err("invalid cycle dates", 422)
        if end < start: return err("cycle ends before it starts", 422)
        parent, team, owner = None, None, me
        ceo = has_permission(cur, me, org, "set_company_goals")
        if level == 1:
            if not ceo: return err("only the CEO sets company goals", 403)
        else:
            cur.execute("SELECT * FROM objectives WHERE id=%s AND org_id=%s", (j.get("parent_id"), org)); parent = cur.fetchone()
            if not parent or parent["level"] != level - 1: return err("link this goal to a goal one level up", 422)
            if level == 2:
                team = j.get("team_id")
                cur.execute("SELECT 1 FROM teams WHERE id=%s AND org_id=%s", (team, org))
                if not cur.fetchone(): return err("unknown team", 422)
                cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s AND is_lead", (org, team, me))
                if not (ceo or cur.fetchone()): return err("only the team lead sets team goals", 403)
            else:
                owner, team = j.get("owner_id") or me, parent["team_id"]
                cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s", (org, team, owner))
                if not cur.fetchone(): return err("a personal goal must hang under a goal of the owner's own team", 422)
                cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s AND is_lead", (org, team, me))
                if not (owner == me or ceo or cur.fetchone()): return err("you can only set goals for yourself", 403)
        cur.execute("""INSERT INTO objectives (org_id, level, title, owner_id, team_id, parent_id, cycle_start, cycle_end, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (org, level, title, owner, team, parent["id"] if parent else None, start, end, me))
        return jsonify(_row(cur.fetchone())), 201

@bp.post("/api/objectives/<int:oid>/key-results")
@login_required
def add_kr(oid):
    me, org = g.user["id"], g.user["org_id"]
    j = request.get_json(silent=True) or {}
    title = (j.get("title") or "").strip()
    try: target, start, current = float(j["target"]), float(j.get("start_value", 0)), float(j.get("current", j.get("start_value", 0)))
    except (KeyError, TypeError, ValueError): return err("target must be a number", 422)
    if not title or target == start: return err("a title and a target different from the start are required", 422)
    with db_cursor() as cur:
        cur.execute("SELECT * FROM objectives WHERE id=%s AND org_id=%s", (oid, org)); o = cur.fetchone()
        if not o or not can_view_objective(cur, me, org, o): return err("not found", 404)
        if not can_edit_objective(cur, me, org, o): return err("you cannot edit this goal", 403)
        owner = j.get("owner_id") or (o["owner_id"] if o["level"] == 3 else me)
        cur.execute("SELECT 1 FROM users WHERE id=%s AND org_id=%s AND active", (owner, org))
        if not cur.fetchone(): return err("unknown owner", 422)
        cur.execute("INSERT INTO key_results (org_id, objective_id, title, owner_id, start_value, target, current) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *", (org, oid, title, owner, start, target, current))
        return jsonify(_row(cur.fetchone())), 201

@bp.patch("/api/key-results/<int:kid>")
@login_required
def update_kr(kid):
    me, org = g.user["id"], g.user["org_id"]
    j = request.get_json(silent=True) or {}
    try: new = float(j["current"])
    except (KeyError, TypeError, ValueError): return err("current must be a number", 422)
    with db_cursor() as cur:
        cur.execute("SELECT k.*, o.level, o.owner_id AS o_owner, o.team_id, o.org_id AS o_org FROM key_results k "
                    "JOIN objectives o ON o.id=k.objective_id WHERE k.id=%s AND k.org_id=%s", (kid, org))
        k = cur.fetchone()
        o = k and {"org_id": k["o_org"], "level": k["level"], "owner_id": k["o_owner"], "team_id": k["team_id"]}
        if not k or not can_view_objective(cur, me, org, o): return err("not found", 404)
        if not (can_edit_objective(cur, me, org, o) or k["owner_id"] == me): return err("you cannot update this key result", 403)
        cur.execute("INSERT INTO kr_updates (org_id, key_result_id, actor_id, old_value, new_value, note) VALUES (%s,%s,%s,%s,%s,%s)",
                    (org, kid, me, k["current"], new, (j.get("note") or "").strip() or None))
        cur.execute("UPDATE key_results SET current=%s, updated_at=now() WHERE id=%s RETURNING *", (new, kid))
        return jsonify(_row(cur.fetchone()))

@bp.get("/api/objectives")
@login_required
def list_objectives():
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        as_of = _asof(cur, org)
        objs, krs, calc = _tree(cur, org, as_of)      # progress uses the whole tree; only visible goals are returned
        out = [_shape(o, krs, calc) for o in objs if can_view_objective(cur, me, org, o)]
    return jsonify(as_of=as_of.isoformat(), objectives=out)

@bp.get("/api/objectives/<int:oid>")
@login_required
def objective(oid):
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        objs, krs, calc = _tree(cur, org, _asof(cur, org))
        o = next((x for x in objs if x["id"] == oid), None)
        if not o or not can_view_objective(cur, me, org, o): return err("not found", 404)
        from .scope import can_view_person
        cur.execute("""SELECT d.id, d.title, d.status, d.owner_id FROM deliverables d JOIN key_results k ON k.id=d.key_result_id
                       WHERE k.objective_id=%s AND d.org_id=%s ORDER BY d.id""", (oid, org))
        linked = [{k: v for k, v in r.items()} for r in cur.fetchall() if can_view_person(cur, me, org, r["owner_id"])]
        return jsonify({**_shape(o, krs, calc), "deliverables": linked})

@bp.post("/api/deliverables/<int:did>/link")
@login_required
def link(did):
    me, org = g.user["id"], g.user["org_id"]
    kid = (request.get_json(silent=True) or {}).get("key_result_id")
    with db_cursor() as cur:
        d = load_deliverable(cur, did)
        if not d: return err("not found", 404)
        if not (d["owner_id"] == me or is_manager(cur, org, me, d["team_id"])): return err("you cannot change this deliverable", 403)
        cur.execute("SELECT o.* FROM key_results k JOIN objectives o ON o.id=k.objective_id WHERE k.id=%s AND k.org_id=%s", (kid, org))
        o = cur.fetchone()
        if not o or not can_view_objective(cur, me, org, o): return err("key result not found", 404)
        if o["level"] == 3 and o["owner_id"] != d["owner_id"]: return err("link to the owner's own personal goals", 422)
        cur.execute("UPDATE deliverables SET key_result_id=%s WHERE id=%s", (kid, did))
        return jsonify(id=did, key_result_id=kid)

@bp.get("/api/checkins/due")
@login_required
def checkins_due():
    """Weekly check-in prompt: key results you own that have not been updated for 7 days."""
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        cur.execute("""SELECT k.id, k.title, k.current, k.target, o.title AS objective, k.updated_at FROM key_results k
                       JOIN objectives o ON o.id=k.objective_id
                       WHERE k.org_id=%s AND k.owner_id=%s AND k.updated_at < now() - interval '7 days' ORDER BY k.id""", (org, me))
        return jsonify(due=[_row(r) for r in cur.fetchall()])

def _form_data(cur, me, org, shown):
    """What the 'new goal' form may offer this person. The API still enforces every rule; this only avoids offering dead ends."""
    ceo = has_permission(cur, me, org, "set_company_goals")
    cur.execute("SELECT t.id, t.name, EXISTS (SELECT 1 FROM team_memberships m WHERE m.team_id=t.id AND m.user_id=%s AND m.is_lead) AS lead "
                "FROM teams t WHERE t.org_id=%s ORDER BY t.name", (me, org))
    teams = [t for t in cur.fetchall() if ceo or t["lead"]]
    members = {}
    for o in shown:
        if o["level"] == 2 and o["team_id"] not in members:
            cur.execute("SELECT u.id, u.full_name AS name FROM team_memberships m JOIN users u ON u.id=m.user_id "
                        "WHERE m.team_id=%s AND m.org_id=%s AND u.active ORDER BY u.full_name", (o["team_id"], org))
            members[o["team_id"]] = cur.fetchall()
    lead_of = {t["id"] for t in teams}
    return {"me": me, "company": ceo, "teams": [{"id": t["id"], "name": t["name"]} for t in teams],
            "parents": [{"id": o["id"], "level": o["level"], "title": o["title"], "team_id": o["team_id"]} for o in shown if o["level"] in (1, 2)],
            "members": {str(k): v for k, v in members.items()}, "lead_of": sorted(lead_of), "ceo": ceo}

@bp.get("/goals")
@page_login
def goals_page():
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        objs, krs, calc = _tree(cur, org, org_today(cur, org))
        shown = []
        for o in objs:
            if can_view_objective(cur, me, org, o):
                s = _shape(o, krs, calc)
                s["editable"] = can_edit_objective(cur, me, org, o)
                for k in s["key_results"]: k["can_update"] = s["editable"] or k["owner_id"] == me
                shown.append(s)
        cur.execute("SELECT k.id, k.title FROM key_results k WHERE k.org_id=%s AND k.owner_id=%s AND k.updated_at < now() - interval '7 days'", (org, me))
        due = cur.fetchall()
        return render_template("goals.html", objectives=shown, due=due, nav=_nav(cur, me, org), form=_form_data(cur, me, org, shown))
