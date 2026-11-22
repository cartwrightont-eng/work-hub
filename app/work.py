"""Work screens: the deliverables board and the page for one deliverable.

These pages only read. Every action (create, start, submit, approve, amend, log, upload, link) is a call to the
existing API from the browser, so the permission rules live in one place and are not repeated here. What these pages
decide is what to *show*: a button appears only when the API would accept it for this person and this status."""
from datetime import timedelta
from flask import Blueprint, g, render_template, request
from .db import db_cursor
from .deliverables import is_manager, is_approver, load
from .metrics import org_today
from .overview import page_login, _nav
from .scope import can_view_person, can_view_objective
from .admin_service import effective_settings

bp = Blueprint("work", __name__)
COLUMNS = [("backlog", "Backlog"), ("in_progress", "In progress"), ("in_review", "In review"), ("done", "Done, last 30 days")]
STATUS_NAMES = {"backlog": "Backlog", "in_progress": "In progress", "in_review": "In review",
                "reopened": "Sent back", "done": "Done"}

def size_options(cur, org):
    s = effective_settings(cur, org)
    return [{"points": p, "label": s["point_labels"].get(str(p), ""), "rubric": s["point_rubric"].get(str(p), "")}
            for p in s["point_scale"]]

def _create_choices(cur, me, org):
    """Teams this person may assign work in, each with its active members."""
    cur.execute("SELECT id, name FROM teams WHERE org_id=%s ORDER BY name", (org,))
    out = []
    for t in cur.fetchall():
        if not is_manager(cur, org, me, t["id"]): continue
        cur.execute("SELECT u.id, u.full_name AS name FROM team_memberships m JOIN users u ON u.id=m.user_id "
                    "WHERE m.team_id=%s AND m.org_id=%s AND u.active ORDER BY u.full_name", (t["id"], org))
        out.append({"id": t["id"], "name": t["name"], "members": cur.fetchall()})
    return out

def board_data(cur, me, org, team_filter=None):
    today = org_today(cur, org)
    cur.execute("""SELECT d.id, d.title, d.points, d.deadline, d.status, d.team_id, d.owner_id, u.full_name AS owner_name,
                   t.name AS team_name,
                   (SELECT max(created_at) FROM status_events e WHERE e.deliverable_id=d.id AND e.to_status='done') AS done_at
                   FROM deliverables d JOIN users u ON u.id=d.owner_id JOIN teams t ON t.id=d.team_id
                   WHERE d.org_id=%s ORDER BY d.deadline, d.id""", (org,))
    rows = cur.fetchall()                      # read these before running any other query on this cursor
    seen, mgr, cols = {}, {}, {k: [] for k, _ in COLUMNS}
    cur.execute("SELECT now() AS n"); now = cur.fetchone()["n"]
    teams = {}
    for d in rows:
        if d["owner_id"] not in seen: seen[d["owner_id"]] = can_view_person(cur, me, org, d["owner_id"])
        if not seen[d["owner_id"]]: continue
        teams[d["team_id"]] = d["team_name"]
        if team_filter and d["team_id"] != team_filter: continue
        if d["status"] == "done" and (not d["done_at"] or d["done_at"] < now - timedelta(days=30)): continue
        if d["team_id"] not in mgr: mgr[d["team_id"]] = is_manager(cur, org, me, d["team_id"])
        owner = d["owner_id"] == me
        acts = []
        if d["status"] == "backlog" and (owner or mgr[d["team_id"]]) and d["points"] is not None: acts.append(("start", "Start"))
        if d["status"] == "in_progress" and owner: acts.append(("submit", "Send for review"))
        if d["status"] == "reopened" and owner: acts.append(("resume", "Resume"))
        d["actions"] = acts
        d["needs_size"] = d["status"] == "backlog" and d["points"] is None
        d["overdue"] = d["status"] != "done" and d["deadline"] < today
        d["can_review"] = d["status"] == "in_review" and is_approver(cur, org, me, d)
        cols["in_progress" if d["status"] == "reopened" else d["status"]].append(d)
    cur.execute("SELECT t.id, t.name FROM teams t JOIN team_memberships m ON m.team_id=t.id WHERE t.org_id=%s AND m.user_id=%s", (org, me))
    for t in cur.fetchall(): teams.setdefault(t["id"], t["name"])
    return {"columns": [(k, label, cols[k]) for k, label in COLUMNS], "teams": sorted(teams.items(), key=lambda x: x[1]),
            "team_filter": team_filter, "today": today}

@bp.get("/work")
@page_login
def board():
    me, org = g.user["id"], g.user["org_id"]
    tf = request.args.get("team", type=int)
    with db_cursor(commit=False) as cur:
        choices = _create_choices(cur, me, org)
        return render_template("work.html", d=board_data(cur, me, org, tf), choices=choices, sizes=size_options(cur, org),
                               default_deadline=(org_today(cur, org) + timedelta(days=7)).isoformat(),
                               nav=_nav(cur, me, org))

@bp.get("/work/<int:did>")
@page_login
def detail_page(did):
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        d = load(cur, did)
        if not d: return render_template("notfound.html"), 404
        cur.execute("SELECT u.full_name FROM users u WHERE u.id=%s", (d["owner_id"],)); owner = cur.fetchone()["full_name"]
        cur.execute("SELECT name FROM teams WHERE id=%s", (d["team_id"],)); team = cur.fetchone()["name"]
        mgr, owns, approver = is_manager(cur, org, me, d["team_id"]), d["owner_id"] == me, is_approver(cur, org, me, d)
        st = d["status"]
        actions = []
        if st == "backlog" and (owns or mgr) and d["points"] is not None: actions.append(("start", "Start work"))
        if st == "in_progress" and owns: actions.append(("submit", "Send for review"))
        if st == "reopened" and owns: actions.append(("resume", "Resume work"))
        cur.execute("""SELECT e.from_status, e.to_status, e.note, e.created_at, u.full_name AS actor FROM status_events e
                       JOIN users u ON u.id=e.actor_id WHERE e.deliverable_id=%s ORDER BY e.id""", (did,))
        events = cur.fetchall()
        cur.execute("""SELECT w.entry_date, w.body, u.full_name AS author FROM work_logs w JOIN users u ON u.id=w.user_id
                       WHERE w.deliverable_id=%s ORDER BY w.id DESC""", (did,))
        logs = cur.fetchall()
        cur.execute("""SELECT a.action, a.detail, a.created_at, u.full_name AS actor FROM audit_log a
                       LEFT JOIN users u ON u.id=a.actor_id
                       WHERE a.org_id=%s AND a.action IN ('deadline_change','points_change') AND a.detail->>'deliverable_id'=%s
                       ORDER BY a.id""", (org, str(did)))
        changes = cur.fetchall()
        cur.execute("SELECT id, name FROM documents WHERE org_id=%s AND parent_type='deliverable' AND parent_id=%s ORDER BY id", (org, did))
        docs = cur.fetchall()
        for doc in docs:
            cur.execute("""SELECT v.version, v.size_bytes, v.uploaded_at, u.full_name AS by FROM document_versions v
                           JOIN users u ON u.id=v.uploaded_by WHERE v.document_id=%s ORDER BY v.version DESC""", (doc["id"],))
            doc["versions"] = cur.fetchall()
        link_choices, linked = [], None
        if owns or mgr:
            cur.execute("""SELECT k.id, k.title AS kr, o.title AS goal, o.level, o.owner_id, o.org_id, o.team_id FROM key_results k
                           JOIN objectives o ON o.id=k.objective_id WHERE k.org_id=%s ORDER BY o.level, o.id, k.id""", (org,))
            for r in cur.fetchall():
                if not can_view_objective(cur, me, org, r): continue
                if r["level"] == 3 and r["owner_id"] != d["owner_id"]: continue
                link_choices.append(r)
        if d["key_result_id"]:
            cur.execute("SELECT k.title AS kr, o.title AS goal FROM key_results k JOIN objectives o ON o.id=k.objective_id WHERE k.id=%s", (d["key_result_id"],))
            linked = cur.fetchone()
        return render_template("work_detail.html", d=d, owner=owner, team=team, status=STATUS_NAMES[st], actions=actions,
                               owns=owns, mgr=mgr, approver=approver and st == "in_review", events=events, logs=logs,
                               changes=changes, docs=docs, sizes=size_options(cur, org), link_choices=link_choices, linked=linked,
                               moved=d["deadline"] != d["original_deadline"], nav=_nav(cur, me, org),
                               status_names=STATUS_NAMES, can_upload=owns or mgr)
