"""Review queue: work waiting for approval. Ordered by how long it has waited, oldest first. This is a to-do list,
not a ranking of people. Approving and sending back reuse the deliverable transition endpoint, so every rule there still applies."""
from flask import Blueprint, jsonify, g, render_template
from .auth import login_required
from .db import db_cursor
from .deliverables import is_approver
from .metrics import org_today
from .overview import page_login, _nav

bp = Blueprint("review", __name__)

def queue_data(cur, me, org):
    today = org_today(cur, org)
    cur.execute("""SELECT d.id, d.title, d.points, d.deadline, d.team_id, d.owner_id, u.full_name AS owner_name, t.name AS team,
        EXTRACT(EPOCH FROM (now() - (SELECT max(created_at) FROM status_events e WHERE e.deliverable_id=d.id AND e.to_status='in_review'))) / 86400.0 AS waiting_days,
        (SELECT count(*) FROM status_events e WHERE e.deliverable_id=d.id AND e.to_status='reopened') AS reopen_count,
        (SELECT count(*) FROM audit_log a WHERE a.org_id=d.org_id AND a.action='deadline_change' AND a.detail->>'deliverable_id'=d.id::text) AS deadline_moved,
        (SELECT body FROM work_logs w WHERE w.deliverable_id=d.id ORDER BY w.id DESC LIMIT 1) AS latest_log,
        EXISTS (SELECT 1 FROM team_memberships m WHERE m.team_id=d.team_id AND m.user_id=d.owner_id AND m.is_lead) AS owner_leads
        FROM deliverables d JOIN users u ON u.id=d.owner_id JOIN teams t ON t.id=d.team_id
        WHERE d.org_id=%s AND d.status='in_review' ORDER BY waiting_days DESC, d.id""", (org,))
    queue, mine = [], []
    for r in cur.fetchall():
        item = {"id": r["id"], "title": r["title"], "owner": {"id": r["owner_id"], "name": r["owner_name"]}, "team": r["team"],
                "points": r["points"], "deadline": r["deadline"].isoformat(), "overdue": r["deadline"] < today,
                "waiting_days": round(float(r["waiting_days"]), 1), "resubmitted": r["reopen_count"] > 0,
                "deadline_moved": r["deadline_moved"], "needs_ceo": r["owner_leads"],
                "latest_log": (r["latest_log"] or "")[:200]}
        if is_approver(cur, org, me, r):
            queue.append(item)
        if r["owner_id"] == me:                                   # your own work that is waiting, and on whom
            if r["owner_leads"]: who = "the CEO"
            else:
                cur.execute("SELECT u.full_name FROM team_memberships m JOIN users u ON u.id=m.user_id "
                            "WHERE m.team_id=%s AND m.org_id=%s AND m.is_lead ORDER BY 1", (r["team_id"], org))
                who = " or ".join(x["full_name"] for x in cur.fetchall()) or "the CEO"
            mine.append({**item, "waiting_on": who})
    return {"queue": queue, "waiting_on_others": mine}

@bp.get("/api/review-queue")
@login_required
def api_queue():
    with db_cursor(commit=False) as cur:
        return jsonify(queue_data(cur, g.user["id"], g.user["org_id"]))

@bp.get("/review")
@page_login
def review_page():
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor(commit=False) as cur:
        return render_template("review.html", d=queue_data(cur, me, org), nav=_nav(cur, me, org))
