from datetime import date, datetime
from flask import Blueprint, jsonify, request, g
from .auth import login_required
from .db import db_cursor
from . import notifications
from .scope import can_view_person, has_permission

bp = Blueprint("deliverables", __name__)
DEFAULT_SCALE = [1, 2, 3, 5, 8]
# action: (allowed from-states, to-state, who may do it)
TRANSITIONS = {
    "start":   ({"backlog"}, "in_progress", "owner_or_manager"),
    "submit":  ({"in_progress"}, "in_review", "owner"),
    "approve": ({"in_review"}, "done", "approver"),
    "reject":  ({"in_review"}, "reopened", "approver"),   # reason required, counts as rework
    "resume":  ({"reopened"}, "in_progress", "owner"),    # restarts the cycle-time clock
}

def err(msg, code): return jsonify(error=msg), code

def ser(row):
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in row.items()}

def point_scale(cur, org):
    cur.execute("SELECT value FROM organisation_settings WHERE org_id=%s AND key='point_scale'", (org,))
    r = cur.fetchone()
    return r["value"] if r else DEFAULT_SCALE

def is_lead(cur, org, uid, team):
    cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND user_id=%s AND team_id=%s AND is_lead",
                (org, uid, team))
    return cur.fetchone() is not None

def is_manager(cur, org, uid, team):
    return is_lead(cur, org, uid, team) or has_permission(cur, uid, org, "manage_teams")

def is_approver(cur, org, uid, d):
    return (is_lead(cur, org, uid, d["team_id"]) and uid != d["owner_id"]) \
        or has_permission(cur, uid, org, "approve_any_deliverable")

def add_event(cur, d, frm, to, actor, note=None):
    cur.execute("INSERT INTO status_events (org_id, deliverable_id, from_status, to_status, actor_id, note) "
                "VALUES (%s,%s,%s,%s,%s,%s)", (d["org_id"], d["id"], frm, to, actor, note))

def audit(cur, d, actor, action, detail):
    cur.execute("INSERT INTO audit_log (org_id, actor_id, action, target_user_id, detail) VALUES (%s,%s,%s,%s,%s)",
                (d["org_id"], actor, action, d["owner_id"], __import__("json").dumps(dict(detail, deliverable_id=d["id"]))))

def load(cur, did):
    """Fetch within the caller's org and scope; None means 'not found' (never reveals existence)."""
    me, org = g.user["id"], g.user["org_id"]
    cur.execute("SELECT * FROM deliverables WHERE id=%s AND org_id=%s", (did, org))
    d = cur.fetchone()
    return d if d and can_view_person(cur, me, org, d["owner_id"]) else None

def parse_deadline(v):
    try: return date.fromisoformat(v)
    except (TypeError, ValueError): return None

@bp.post("/api/deliverables")
@login_required
def create():
    me, org = g.user["id"], g.user["org_id"]
    j = request.get_json(silent=True) or {}
    title, deadline = (j.get("title") or "").strip(), parse_deadline(j.get("deadline"))
    if not title or not deadline: return err("title and a valid deadline (YYYY-MM-DD) are required", 422)
    with db_cursor() as cur:
        team, owner = j.get("team_id"), j.get("owner_id")
        cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s", (org, team, owner))
        if not cur.fetchone(): return err("owner must belong to that team", 422)
        if not is_manager(cur, org, me, team): return err("only the team lead can assign work", 403)
        pts = j.get("points")
        if pts is not None and pts not in point_scale(cur, org):
            return err(f"points must be one of {point_scale(cur, org)}", 422)
        cur.execute("""INSERT INTO deliverables (org_id, team_id, owner_id, created_by, title, description,
                       points, original_deadline, deadline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (org, team, owner, me, title, j.get("description") or "", pts, deadline, deadline))
        d = cur.fetchone()
        add_event(cur, d, None, "backlog", me)
    return jsonify(ser(d)), 201

@bp.get("/api/deliverables")
@login_required
def list_all():
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM deliverables WHERE org_id=%s ORDER BY id", (org,))
        rows = [d for d in cur.fetchall() if can_view_person(cur, me, org, d["owner_id"])]
    return jsonify(deliverables=[ser(r) for r in rows])

@bp.get("/api/deliverables/<int:did>")
@login_required
def detail(did):
    with db_cursor(commit=False) as cur:
        d = load(cur, did)
        if not d: return err("not found", 404)
        cur.execute("SELECT from_status, to_status, actor_id, note, created_at FROM status_events "
                    "WHERE deliverable_id=%s ORDER BY id", (did,))
        events = [ser(e) for e in cur.fetchall()]
        cur.execute("SELECT actor_id, action, detail, created_at FROM audit_log WHERE org_id=%s AND detail->>'deliverable_id'=%s ORDER BY id",
                    (d["org_id"], str(did)))
        changes = [ser(c) for c in cur.fetchall()]
    return jsonify(dict(ser(d), events=events, changes=changes))

@bp.post("/api/deliverables/<int:did>/transition")
@login_required
def transition(did):
    me, org = g.user["id"], g.user["org_id"]
    j = request.get_json(silent=True) or {}
    action, reason = j.get("action"), (j.get("reason") or "").strip()
    if action not in TRANSITIONS: return err("unknown action", 422)
    frm, to, who = TRANSITIONS[action]
    with db_cursor() as cur:
        d = load(cur, did)
        if not d: return err("not found", 404)
        allowed = {"owner": me == d["owner_id"],
                   "owner_or_manager": me == d["owner_id"] or is_manager(cur, org, me, d["team_id"]),
                   "approver": is_approver(cur, org, me, d)}[who]
        if not allowed: return err("you cannot do that on this deliverable", 403)
        if d["status"] not in frm: return err(f"cannot {action} from {d['status']}", 409)
        if action == "reject" and not reason: return err("a reason is required when sending work back", 422)
        if action == "start" and d["points"] is None: return err("size the deliverable before starting", 422)
        cur.execute("UPDATE deliverables SET status=%s WHERE id=%s", (to, did))
        add_event(cur, d, d["status"], to, me, reason or None)
        if action == "submit": notifications.on_submitted(cur, d)
    try: notifications.deliver_pending(20)          # best effort; anything that fails is retried by notify.py
    except Exception: pass
    return jsonify(id=did, status=to)

@bp.patch("/api/deliverables/<int:did>")
@login_required
def amend(did):
    """Deadline or points change. Lead-only, always with a reason, always audited.
    The original deadline is kept untouched for the on-time metric."""
    me, org = g.user["id"], g.user["org_id"]
    j = request.get_json(silent=True) or {}
    reason = (j.get("reason") or "").strip()
    with db_cursor() as cur:
        d = load(cur, did)
        if not d: return err("not found", 404)
        if not is_manager(cur, org, me, d["team_id"]): return err("only the team lead can change this", 403)
        if d["status"] == "done": return err("completed work cannot be changed", 409)
        if not reason: return err("a reason is required", 422)
        if "deadline" in j:
            new = parse_deadline(j["deadline"])
            if not new: return err("invalid deadline", 422)
            cur.execute("UPDATE deliverables SET deadline=%s WHERE id=%s", (new, did))
            audit(cur, d, me, "deadline_change", {"old": d["deadline"].isoformat(), "new": new.isoformat(),
                                                  "reason": reason, "after_start": d["status"] != "backlog"})
        if "points" in j:
            if j["points"] not in point_scale(cur, org): return err("points not on the scale", 422)
            cur.execute("UPDATE deliverables SET points=%s WHERE id=%s", (j["points"], did))
            audit(cur, d, me, "points_change", {"old": d["points"], "new": j["points"], "reason": reason,
                                                "after_start": d["status"] != "backlog"})
        cur.execute("SELECT * FROM deliverables WHERE id=%s", (did,))
        return jsonify(ser(cur.fetchone()))

@bp.post("/api/deliverables/<int:did>/worklog")
@login_required
def add_log(did):
    me = g.user["id"]
    body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
    if not body or len(body) > 2000: return err("entry must be 1 to 2000 characters", 422)
    with db_cursor() as cur:
        d = load(cur, did)
        if not d: return err("not found", 404)
        if d["owner_id"] != me: return err("you can only log work on your own deliverables", 403)
        cur.execute("INSERT INTO work_logs (org_id, deliverable_id, user_id, body) VALUES (%s,%s,%s,%s) RETURNING *",
                    (d["org_id"], did, me, body))
        return jsonify(ser(cur.fetchone())), 201

@bp.get("/api/deliverables/<int:did>/worklog")
@login_required
def get_logs(did):
    with db_cursor(commit=False) as cur:
        if not load(cur, did): return err("not found", 404)
        cur.execute("SELECT user_id, entry_date, body FROM work_logs WHERE deliverable_id=%s ORDER BY id", (did,))
        return jsonify(entries=[ser(r) for r in cur.fetchall()])
