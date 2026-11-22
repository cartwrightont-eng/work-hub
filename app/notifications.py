from datetime import timedelta
import logging, os
from . import mailer
from .admin_service import effective_settings
from .db import db_cursor
from .metrics import org_today

log = logging.getLogger("notifications")

def _link(path):
    base = os.environ.get("APP_URL", "").rstrip("/")
    return f"\nOpen it here: {base}{path}\n" if base else ""

def _queue(cur, org, user_id, kind, d, subject, body, key):
    cur.execute("SELECT 1 FROM users WHERE id=%s AND org_id=%s AND active AND email IS NOT NULL", (user_id, org))
    if cur.fetchone():                        # people without an email address are simply not emailed
        cur.execute("""INSERT INTO notifications (org_id, user_id, kind, deliverable_id, subject, body, dedupe_key)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING""",
                    (org, user_id, kind, d["id"], mailer.clean_subject(subject), body, key))

def _approvers(cur, d):
    """The team's lead(s) other than the owner; for a lead's own work (no other lead), whoever may approve anything."""
    cur.execute("SELECT user_id FROM team_memberships WHERE org_id=%s AND team_id=%s AND is_lead AND user_id<>%s",
                (d["org_id"], d["team_id"], d["owner_id"]))
    ids = [r["user_id"] for r in cur.fetchall()]
    if ids: return ids
    cur.execute("""SELECT DISTINCT ur.user_id FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id AND rp.org_id=ur.org_id
                   JOIN permissions p ON p.id=rp.permission_id WHERE ur.org_id=%s AND p.key='approve_any_deliverable'""", (d["org_id"],))
    return [r["user_id"] for r in cur.fetchall()]

def on_submitted(cur, d):
    """Called inside the transaction that moves work to In review."""
    if not effective_settings(cur, d["org_id"])["notifications_enabled"]: return
    cur.execute("SELECT max(id) AS e FROM status_events WHERE deliverable_id=%s", (d["id"],)); ev = cur.fetchone()["e"]
    cur.execute("SELECT full_name FROM users WHERE id=%s", (d["owner_id"],)); owner = cur.fetchone()["full_name"]
    pts = f", {d['points']} points" if d["points"] is not None else ""
    body = f"{owner} has sent \"{d['title']}\" for review ({'due ' + d['deadline'].isoformat()}{pts}).\n{_link('/review')}"
    for uid in _approvers(cur, d):
        _queue(cur, d["org_id"], uid, "review", d, f"Review needed: {d['title']}", body, f"review:{d['id']}:{ev}:{uid}")

def scan_due(cur, today=None):
    """Queue due-soon and overdue reminders. Safe to run as often as you like: each fires once per deliverable, deadline and person."""
    cur.execute("SELECT id FROM organisations ORDER BY id")
    for org in [r["id"] for r in cur.fetchall()]:
        s = effective_settings(cur, org)
        if not s["notifications_enabled"]: continue
        t = today or org_today(cur, org)
        cols = "id, org_id, team_id, owner_id, title, points, deadline"
        if s["notify_due_soon_days"] > 0:
            cur.execute(f"SELECT {cols} FROM deliverables WHERE org_id=%s AND status<>'done' AND deadline BETWEEN %s AND %s",
                        (org, t, t + timedelta(days=s["notify_due_soon_days"])))
            for d in cur.fetchall():
                n = (d["deadline"] - t).days
                when = "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
                _queue(cur, org, d["owner_id"], "due_soon", d, f"Due soon: {d['title']}",
                       f"\"{d['title']}\" is due {when}, on {d['deadline'].isoformat()}.\n{_link('/my-work')}",
                       f"due_soon:{d['id']}:{d['deadline']}:{d['owner_id']}")
        cur.execute(f"SELECT {cols} FROM deliverables WHERE org_id=%s AND status<>'done' AND deadline < %s", (org, t))
        for d in cur.fetchall():
            body = f"\"{d['title']}\" was due on {d['deadline'].isoformat()} and is not marked done yet.\n{_link('/my-work')}"
            for uid in [d["owner_id"], *_approvers(cur, d)]:
                _queue(cur, org, uid, "overdue", d, f"Overdue: {d['title']}", body, f"overdue:{d['id']}:{d['deadline']}:{uid}")

def deliver_pending(limit=50):
    """Send what is queued. A failure is recorded and retried later; it never raises into the caller."""
    sent = failed = 0
    with db_cursor() as cur:
        cur.execute("""SELECT n.id, n.subject, n.body, u.email FROM notifications n JOIN users u ON u.id=n.user_id
                       WHERE n.sent_at IS NULL AND n.attempts < 5 AND u.email IS NOT NULL ORDER BY n.id LIMIT %s
                       FOR UPDATE OF n SKIP LOCKED""", (limit,))
        for n in cur.fetchall():
            try:
                mailer.send(n["email"], n["subject"], n["body"])
                cur.execute("UPDATE notifications SET sent_at=now(), attempts=attempts+1, error=NULL WHERE id=%s", (n["id"],)); sent += 1
            except Exception as e:
                log.warning("notification %s failed: %s", n["id"], e)
                cur.execute("UPDATE notifications SET attempts=attempts+1, error=%s WHERE id=%s", (str(e)[:300], n["id"])); failed += 1
    return sent, failed
