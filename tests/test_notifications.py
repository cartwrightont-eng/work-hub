import smtplib
from datetime import date, timedelta
import pytest
from app import mailer, notifications
from app.db import db_cursor
from app.metrics import org_today
from seed import seed_org
from tests.conftest import PW
from tests.test_deliverables import mk, act

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://hub.example")
    mailer.OUTBOX.clear()
    with db_cursor() as cur: cur.execute("DELETE FROM notifications")

@pytest.fixture
def w(login):
    return {n: login(e) for n, e in [("ceo", "ceo@homerr.test"), ("trevor", "trevor@x.test"), ("georgina", "georgina@x.test"), ("peter", "peter@x.test")]}

def rows(*dids):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT n.kind, n.deliverable_id AS d, u.email, n.sent_at IS NOT NULL AS sent FROM notifications n "
                    "JOIN users u ON u.id=n.user_id WHERE n.deliverable_id = ANY(%s) ORDER BY n.id", (list(dids),))
        return cur.fetchall()

def submit(w, ids, owner="Georgina", who="georgina", **kw):
    did = mk(w["trevor"], ids, owner=owner, **kw).get_json()["id"]
    act(w[who], did, "start"); assert act(w[who], did, "submit").status_code == 200
    return did

def test_review_email_goes_to_the_people_who_can_approve(w, ids):
    did = submit(w, ids, title="Notify item")
    assert [m[0] for m in mailer.OUTBOX] == ["trevor@x.test"]                        # her lead only: not Peter, not Laura, not the CEO
    to, subject, body = mailer.OUTBOX[0]
    assert subject == "Review needed: Notify item" and "Georgina" in body and "https://hub.example/review" in body
    own = submit(w, ids, owner="Trevor", who="trevor", title="Lead item")
    assert [m[0] for m in mailer.OUTBOX][1:] == ["ceo@homerr.test"]                  # a lead's own work goes to the CEO
    assert [(r["kind"], r["email"], r["sent"]) for r in rows(did)] == [("review", "trevor@x.test", True)]
    assert notifications.deliver_pending() == (0, 0) and len(mailer.OUTBOX) == 2     # nothing is ever sent twice
    assert act(w["trevor"], did, "reject", "More detail").status_code == 200
    act(w["georgina"], did, "resume"); act(w["georgina"], did, "submit")
    assert len(rows(did)) == 2 and len(mailer.OUTBOX) == 3                           # a resubmission is a new request for review

def test_failed_sending_never_breaks_the_request_and_is_retried(w, ids, monkeypatch):
    did = mk(w["trevor"], ids, title="Retry item").get_json()["id"]
    act(w["georgina"], did, "start")
    def boom(*a): raise RuntimeError("smtp down")
    with monkeypatch.context() as m:
        m.setattr(mailer, "send", boom)
        assert act(w["georgina"], did, "submit").status_code == 200                  # the work still moves
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT attempts, error, sent_at FROM notifications WHERE deliverable_id=%s", (did,)); n = cur.fetchone()
    assert (n["attempts"], n["error"], n["sent_at"]) == (1, "smtp down", None)
    assert notifications.deliver_pending() == (1, 0) and [m[0] for m in mailer.OUTBOX] == ["trevor@x.test"]

def test_due_soon_and_overdue_fire_once_per_deadline(w, ids):
    with db_cursor(commit=False) as cur: today = org_today(cur, ids["org"])
    day = lambda n: (today + timedelta(days=n)).isoformat()
    soon, far = [mk(w["trevor"], ids, title=t, deadline=day(n)).get_json()["id"] for t, n in (("Soon item", 1), ("Far item", 10))]
    over = mk(w["trevor"], ids, title="Over item", deadline=day(-2)).get_json()["id"]
    back = mk(w["trevor"], ids, title="Backlog over item", deadline=day(-3)).get_json()["id"]
    done = mk(w["trevor"], ids, title="Done item", deadline=day(-2)).get_json()["id"]
    act(w["georgina"], over, "start")
    with db_cursor() as cur: cur.execute("UPDATE deliverables SET status='done' WHERE id=%s", (done,))
    scan = lambda: [cur_scan() for cur_scan in [lambda: _scan()]]
    def _scan():
        with db_cursor() as cur: notifications.scan_due(cur)
    mine = (soon, far, over, back, done)
    _scan()
    got = sorted((r["kind"], r["d"], r["email"]) for r in rows(*mine))
    assert got == sorted([("due_soon", soon, "georgina@x.test"), ("overdue", over, "georgina@x.test"), ("overdue", over, "trevor@x.test"),
                          ("overdue", back, "georgina@x.test"), ("overdue", back, "trevor@x.test")])    # not far, not done
    _scan(); assert len(rows(*mine)) == 5                                            # running again adds nothing
    assert w["trevor"].patch(f"/api/deliverables/{over}", json={"deadline": day(-1), "reason": "slipped"}).status_code == 200
    _scan(); assert len([r for r in rows(over) if r["kind"] == "overdue"]) == 4     # a new deadline gets a fresh reminder
    notifications.deliver_pending(200)
    soon_mail = next(m for m in mailer.OUTBOX if m[1] == "Due soon: Soon item")
    assert soon_mail[0] == "georgina@x.test" and "tomorrow" in soon_mail[2] and day(1) in soon_mail[2]
    assert any(m[1] == "Overdue: Over item" and "is not marked done yet" in m[2] for m in mailer.OUTBOX)

def test_settings_switch_notifications_off_and_validate(w, ids):
    ceo = w["ceo"]
    for bad in ({"notify_due_soon_days": 15}, {"notify_due_soon_days": -1}, {"notify_due_soon_days": "2"}, {"notifications_enabled": "yes"}):
        assert ceo.put("/api/settings", json=bad).status_code == 422, bad
    with db_cursor(commit=False) as cur: today = org_today(cur, ids["org"])
    try:
        assert ceo.put("/api/settings", json={"notify_due_soon_days": 0}).status_code == 200
        soon = mk(w["trevor"], ids, title="No reminder", deadline=(today + timedelta(days=1)).isoformat()).get_json()["id"]
        with db_cursor() as cur: notifications.scan_due(cur)
        assert rows(soon) == []                                                      # due-soon reminders are off
        with db_cursor() as cur: cur.execute("DELETE FROM notifications")           # overdue reminders for old items are separate from the due-soon switch
        mailer.OUTBOX.clear()
        assert ceo.put("/api/settings", json={"notifications_enabled": False}).status_code == 200
        did = submit(w, ids, title="Silent item")
        with db_cursor() as cur: notifications.scan_due(cur)
        assert rows(did) == [] and mailer.OUTBOX == []                               # everything is off
    finally:
        ceo.put("/api/settings", json={"notifications_enabled": True, "notify_due_soon_days": 2})

def test_subject_cannot_be_used_to_inject_headers(w, ids):
    did = submit(w, ids, title="Hello\r\nBcc: evil@example.com")
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT subject FROM notifications WHERE deliverable_id=%s", (did,)); s = cur.fetchone()["subject"]
    assert "\r" not in s and "\n" not in s and mailer.OUTBOX[0][1] == s
    with pytest.raises(ValueError): mailer.send("a@b.co\r\nBcc: evil@example.com", "x", "y")

def test_people_without_an_email_address_are_not_emailed(login):
    spec = {"organisation": {"name": "Notify Co", "slug": "notify-co"}, "ceo": {"name": "Nora"},
            "teams": [{"name": "Core", "lead": "Nia", "members": [{"name": "Ned", "reports_to": "Nia"}]}]}
    with db_cursor() as cur:
        org = seed_org(cur, spec, "nora@notify.test", PW)
        cur.execute("SELECT id FROM users WHERE org_id=%s AND full_name='Ned'", (org,)); ned = cur.fetchone()["id"]
        cur.execute("SELECT id FROM teams WHERE org_id=%s", (org,)); team = cur.fetchone()["id"]
    ceo = login("nora@notify.test")
    assert ceo.patch(f"/api/admin/users/{ned}", json={"email": "ned@notify.test", "password": PW}).status_code == 200
    did = ceo.post("/api/deliverables", json={"title": "x", "owner_id": ned, "team_id": team, "points": 2, "deadline": "2030-01-01"}).get_json()["id"]
    ned_c = login("ned@notify.test")
    act(ned_c, did, "start"); assert act(ned_c, did, "submit").status_code == 200
    assert rows(did) == [] and mailer.OUTBOX == []                                   # Nia has no address, so nobody is emailed

def test_smtp_backend_builds_a_proper_message(monkeypatch):
    calls = []
    class FakeSMTP:
        def __init__(self, host, port, timeout): calls.append(("connect", host, port, timeout))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): calls.append(("starttls",))
        def login(self, u, p): calls.append(("login", u, p))
        def send_message(self, msg): calls.append(("send", msg["From"], msg["To"], msg["Subject"], msg.get_content().strip()))
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for k, v in {"MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.example", "SMTP_PORT": "2525", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SMTP_FROM": "Hub <hub@example.com>"}.items():
        monkeypatch.setenv(k, v)
    mailer.send("a@b.co", "Hi\r\nBcc: x", "hello there")
    assert calls == [("connect", "smtp.example", 2525, 10), ("starttls",), ("login", "u", "p"),
                     ("send", "Hub <hub@example.com>", "a@b.co", "Hi Bcc: x", "hello there")]
