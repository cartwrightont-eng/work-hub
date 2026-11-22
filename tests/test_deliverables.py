from app.db import db_cursor

def mk(c, ids, owner="Georgina", team="Tech", **kw):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT id FROM teams WHERE name=%s AND org_id=%s", (team, ids["org"]))
        tid = cur.fetchone()["id"]
    body = dict(title="Fix listing upload", owner_id=ids[owner], team_id=tid, points=3, deadline="2026-12-01")
    body.update(kw)
    return c.post("/api/deliverables", json=body)

def act(c, did, action, reason=None):
    return c.post(f"/api/deliverables/{did}/transition", json={"action": action, "reason": reason})

def events(did):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT from_status, to_status, actor_id, note FROM status_events WHERE deliverable_id=%s ORDER BY id", (did,))
        return [tuple(r.values()) for r in cur.fetchall()]

def test_full_lifecycle_produces_correct_events(login, ids):
    lead, g_ = login("trevor@x.test"), login("georgina@x.test")
    r = mk(lead, ids); assert r.status_code == 201
    did, T, G = r.get_json()["id"], ids["Trevor"], ids["Georgina"]
    assert act(g_, did, "start").status_code == 200
    assert act(g_, did, "submit").status_code == 200
    assert act(lead, did, "reject", "Fix the off-by-one").status_code == 200
    assert act(g_, did, "resume").status_code == 200
    assert act(g_, did, "submit").status_code == 200
    assert act(lead, did, "approve").status_code == 200
    assert events(did) == [(None, "backlog", T, None), ("backlog", "in_progress", G, None),
        ("in_progress", "in_review", G, None), ("in_review", "reopened", T, "Fix the off-by-one"),
        ("reopened", "in_progress", G, None), ("in_progress", "in_review", G, None), ("in_review", "done", T, None)]
    d = lead.get(f"/api/deliverables/{did}").get_json()
    assert d["status"] == "done" and len(d["events"]) == 7

def test_approval_rules(login, ids):
    lead, g_, ceo = login("trevor@x.test"), login("georgina@x.test"), login("ceo@homerr.test")
    did = mk(lead, ids).get_json()["id"]; act(g_, did, "start"); act(g_, did, "submit")
    assert act(g_, did, "approve").status_code == 403           # owner cannot self-approve
    assert act(lead, did, "reject").status_code == 422          # reason required
    own = mk(lead, ids, owner="Trevor").get_json()["id"]; act(lead, own, "start"); act(lead, own, "submit")
    assert act(lead, own, "approve").status_code == 403         # lead's own work needs the CEO
    assert act(ceo, own, "approve").status_code == 200

def test_invalid_transitions_and_sizing(login, ids):
    lead, g_ = login("trevor@x.test"), login("georgina@x.test")
    did = mk(lead, ids).get_json()["id"]
    assert act(g_, did, "submit").status_code == 409 and act(g_, did, "approve").status_code == 403
    unsized = mk(lead, ids, points=None).get_json()["id"]
    assert act(g_, unsized, "start").status_code == 422
    assert mk(lead, ids, points=4).status_code == 422
    assert mk(lead, ids, deadline="soon").status_code == 422

def test_deadline_and_points_changes_are_audited(login, ids):
    lead, g_ = login("trevor@x.test"), login("georgina@x.test")
    did = mk(lead, ids).get_json()["id"]; act(g_, did, "start")
    p = lambda c, **j: c.patch(f"/api/deliverables/{did}", json=j)
    assert p(g_, deadline="2026-12-20", reason="more time").status_code == 403
    assert p(lead, deadline="2026-12-20").status_code == 422
    r = p(lead, deadline="2026-12-20", points=5, reason="scope grew")
    assert r.status_code == 200 and r.get_json()["deadline"] == "2026-12-20"
    assert r.get_json()["original_deadline"] == "2026-12-01"
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT action, detail FROM audit_log WHERE detail->>'deliverable_id'=%s ORDER BY id", (str(did),))
        rows = cur.fetchall()
    assert [x["action"] for x in rows] == ["deadline_change", "points_change"]
    assert rows[0]["detail"]["reason"] == "scope grew" and rows[0]["detail"]["after_start"] is True

def test_visibility_and_idor(login, ids):
    lead = login("trevor@x.test")
    did = mk(lead, ids).get_json()["id"]
    for who in ("peter@x.test", "laura@x.test", "ceo@other.test"):
        c = login(who)
        assert c.get(f"/api/deliverables/{did}").status_code == 404
        assert act(c, did, "start").status_code == 404
        assert did not in [d["id"] for d in c.get("/api/deliverables").get_json()["deliverables"]]
    assert login("ceo@homerr.test").get(f"/api/deliverables/{did}").status_code == 200
    assert login("peter@x.test").post("/api/deliverables", json={}).status_code in (403, 422)
    assert mk(login("laura@x.test"), ids).status_code == 403   # other team's lead cannot assign into Tech

def test_work_log(login, ids):
    lead, g_ = login("trevor@x.test"), login("georgina@x.test")
    did = mk(lead, ids).get_json()["id"]
    assert g_.post(f"/api/deliverables/{did}/worklog", json={"body": "Reproduced the bug"}).status_code == 201
    assert lead.post(f"/api/deliverables/{did}/worklog", json={"body": "x"}).status_code == 403
    assert g_.post(f"/api/deliverables/{did}/worklog", json={"body": " "}).status_code == 422
    assert [e["body"] for e in lead.get(f"/api/deliverables/{did}/worklog").get_json()["entries"]] == ["Reproduced the bug"]
    assert login("peter@x.test").get(f"/api/deliverables/{did}/worklog").status_code == 404
