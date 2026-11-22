"""Phase 7 acceptance: a second organisation with different teams and a different sizing scale works with no code change."""
import json, pathlib
import pytest
from pytest import approx
from app.db import db_cursor
from app import admin_service as svc
from seed import seed_org
from tests.conftest import PW
from tests.test_deliverables import mk
from tests.test_documents import up, PDF, SAMPLES

ZEPHYR = {
 "organisation": {"name": "Zephyr Studio", "slug": "zephyr"},
 "settings": {"timezone": "America/New_York", "point_scale": [1, 2, 4, 8, 16],
              "point_labels": {"1": "XS", "2": "S", "4": "M", "8": "L", "16": "XL"},
              "point_rubric": {"1": "quick fix", "16": "a quarter's work"},
              "file_types": ["pdf", "png"], "max_file_mb": 1, "reopen_rule": "first_start", "on_time_band": [0.7, 0.95]},
 "ceo": {"name": "Zed"},
 "teams": [{"name": "Design", "lead": "Dana", "members": [{"name": "Dev", "reports_to": "Dana"}, {"name": "Drew", "reports_to": "Dana"}]},
           {"name": "Support", "lead": "Sol", "members": []}, {"name": "Growth", "lead": "Gia", "members": []}]}

@pytest.fixture(scope="module")
def zep(ids):
    with db_cursor() as cur:
        org = seed_org(cur, ZEPHYR, "zed@zephyr.test", PW)
        cur.execute("SELECT id, full_name FROM users WHERE org_id=%s", (org,)); u = {r["full_name"]: r["id"] for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM teams WHERE org_id=%s", (org,)); t = {r["name"]: r["id"] for r in cur.fetchall()}
    return {"org": org, "u": u, "t": t}

@pytest.fixture
def w(zep, login):
    ceo = login("zed@zephyr.test")                       # people get their logins through the admin screen, not through code
    for n in ("Dana", "Dev", "Drew", "Sol", "Gia"):
        assert ceo.patch(f"/api/admin/users/{zep['u'][n]}", json={"email": f"{n.lower()}@zephyr.test", "password": PW}).status_code == 200
    return {"ceo": ceo, **{n: login(f"{n}@zephyr.test") for n in ("dana", "dev", "drew", "sol", "gia")}}

def deliv(c, zep, team, owner, pts):
    return c.post("/api/deliverables", json={"title": "T", "owner_id": zep["u"][owner], "team_id": zep["t"][team], "points": pts, "deadline": "2026-12-01"})

def test_second_organisation_runs_on_its_own_configuration(w, zep, login, ids):
    # different sizing scale, enforced per organisation
    assert deliv(w["dana"], zep, "Design", "Dev", 16).status_code == 201
    assert deliv(w["dana"], zep, "Design", "Dev", 5).status_code == 422
    assert mk(login("trevor@x.test"), ids, points=16).status_code == 422          # the first organisation is unaffected
    assert mk(login("trevor@x.test"), ids, points=5).status_code == 201
    s = w["dana"].get("/api/sizing").get_json()
    assert s["scale"] == [1, 2, 4, 8, 16] and s["labels"]["16"] == "XL" and s["rubric"]["1"] == "quick fix"
    assert login("trevor@x.test").get("/api/sizing").get_json()["scale"] == [1, 2, 3, 5, 8]
    # different teams and hierarchy
    org = w["ceo"].get("/api/org/overview").get_json()
    assert [t["name"] for t in org["teams"]] == ["Design", "Growth", "Support"]
    assert [p["name"] for p in org["people"]] == ["Dana", "Dev", "Drew", "Gia", "Sol", "Zed"]
    code = lambda c, n: c.get(f"/api/people/{zep['u'][n]}").status_code
    assert (code(w["dana"], "Dev"), code(w["dana"], "Drew"), code(w["dana"], "Sol")) == (200, 200, 404)
    assert code(w["sol"], "Dev") == 404 and code(w["dev"], "Dana") == 404
    # different file policy
    assert up(w["dana"], "team", zep["t"]["Design"], "logo.png", SAMPLES["png"][0], "image/png").status_code == 201
    assert up(w["dana"], "team", zep["t"]["Design"], "plan.docx", *SAMPLES["docx"]).status_code == 415
    assert up(w["dana"], "team", zep["t"]["Design"], "big.pdf", PDF + b"0" * (1024 * 1024)).status_code == 413

def test_timezone_and_reopen_rule_come_from_settings(w, zep):
    did = deliv(w["dana"], zep, "Design", "Dev", 2).get_json()["id"]
    D = zep["u"]["Dana"]
    evs = [(None, "backlog", "10-30 06:00"), ("backlog", "in_progress", "11-01 06:00"), ("in_progress", "in_review", "11-02 06:00"),
           ("in_review", "reopened", "11-02 12:00"), ("reopened", "in_progress", "11-05 06:00"),
           ("in_progress", "in_review", "11-05 12:00"), ("in_review", "done", "11-06 03:30")]
    with db_cursor() as cur:
        cur.execute("DELETE FROM status_events WHERE deliverable_id=%s", (did,))
        for f, t, ts in evs:
            cur.execute("INSERT INTO status_events (org_id, deliverable_id, from_status, to_status, actor_id, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (zep["org"], did, f, t, D, f"2026-{ts}+00"))
    facts = lambda: next(iter(_q("SELECT cycle_days, done_local_date::text AS d FROM v_deliverable_facts WHERE id=%s", did)))
    f = facts()
    assert f["d"] == "2026-11-05"                                  # 03:30 UTC is 22:30 the day before in New York
    assert float(f["cycle_days"]) == approx(4.8958, abs=1e-3)       # first_start: Nov 1 06:00 to Nov 6 03:30
    try:
        assert w["ceo"].put("/api/settings", json={"reopen_rule": "restart"}).status_code == 200
        assert float(facts()["cycle_days"]) == approx(0.8958, abs=1e-3)   # restart: Nov 5 06:00 to Nov 6 03:30
    finally:
        w["ceo"].put("/api/settings", json={"reopen_rule": "first_start"})

def _q(sql, *p):
    with db_cursor(commit=False) as cur:
        cur.execute(sql, p); return cur.fetchall()

def test_no_organisation_specific_values_in_code():
    root = pathlib.Path(__file__).resolve().parents[1]
    files = [*root.glob("app/*.py"), *root.glob("app/templates/*.html"), root / "app/schema.sql", root / "seed.py"]
    banned = ["homerr", "sebastian", "rague", "cartwright", "trevor", "georgina", "laura", "naaz", "michael"]
    hits = [(f.name, b) for f in files for b in banned if b in f.read_text().lower()]
    assert hits == []
    assert "homerr" in (root / "seeds/homerr.json").read_text().lower()      # the organisation lives in data only

def test_settings_validation_permissions_and_isolation(w, zep, login):
    for who in ("dana", "dev"):
        c = w[who]
        assert c.get("/api/settings").status_code == 404 and c.get("/settings").status_code == 404
        assert c.put("/api/settings", json={"max_file_mb": 5}).status_code == 404
        assert c.post("/api/admin/teams", json={"name": "x"}).status_code == 404 and c.get("/api/admin/roles").status_code == 404
    bad = [{"timezone": "Mars/Base"}, {"point_scale": [3, 2, 1]}, {"point_scale": [0, 1]}, {"point_scale": list(range(1, 10))},
           {"file_types": ["exe"]}, {"max_file_mb": 99}, {"reopen_rule": "never"}, {"mystery": 1}, {"week_start": "sunday"},
           {"peer_visibility": "yes"}, {"on_time_band": [0.9, 0.8]}, {}]
    for b in bad: assert w["ceo"].put("/api/settings", json=b).status_code == 422, b
    assert w["ceo"].put("/api/settings", json={"max_file_mb": 2, "timezone": "bad"}).status_code == 422
    assert w["ceo"].get("/api/settings").get_json()["max_file_mb"] == 1                 # nothing half-applied
    try:
        assert w["ceo"].put("/api/settings", json={"on_time_band": [0.6, 0.9]}).get_json()["on_time_band"] == [0.6, 0.9]
        row = _q("SELECT detail FROM audit_log WHERE org_id=%s AND action='settings_change' ORDER BY id DESC LIMIT 1", zep["org"])[0]
        assert row["detail"]["changed"]["on_time_band"] == {"old": [0.7, 0.95], "new": [0.6, 0.9]}
        assert "0.9" in w["ceo"].get(f"/people/{zep['u']['Dev']}").get_data(as_text=True)    # the dashboard reads the band from settings
        other = login("ceo@homerr.test")
        assert other.put("/api/settings", json={"on_time_band": [0.5, 0.7]}).status_code == 200
        assert w["ceo"].get("/api/settings").get_json()["on_time_band"] == [0.6, 0.9]    # one organisation's change never touches another
    finally:
        w["ceo"].put("/api/settings", json={"on_time_band": [0.7, 0.95]}); login("ceo@homerr.test").put("/api/settings", json={"on_time_band": [0.8, 0.9]})

def test_peer_visibility_switch_changes_visibility_on_the_next_request(w, zep, login, ids):
    code = lambda c, n: c.get(f"/api/people/{zep['u'][n]}").status_code
    assert code(w["dev"], "Drew") == 404
    try:
        assert w["ceo"].put("/api/settings", json={"peer_visibility": True}).status_code == 200
        assert (code(w["dev"], "Drew"), code(w["drew"], "Dev")) == (200, 200)
        assert code(w["dev"], "Dana") == 404 and code(w["dev"], "Sol") == 404            # the lead's page stays private; other teams stay closed
        assert login("peter@x.test").get(f"/api/people/{ids['Georgina']}").status_code == 404     # the first organisation keeps its own setting
    finally:
        w["ceo"].put("/api/settings", json={"peer_visibility": False})
    assert code(w["dev"], "Drew") == 404

def test_admin_endpoints_build_teams_roles_and_people(w, zep, login, ids):
    ceo = w["ceo"]
    research = ceo.post("/api/admin/teams", json={"name": "Research"}).get_json()["id"]
    assert ceo.post("/api/admin/teams", json={"name": "Research"}).status_code == 422                 # duplicate
    assert ceo.post("/api/admin/roles", json={"name": "reviewer", "permissions": ["view_all_scorecards"]}).status_code == 201
    assert ceo.post("/api/admin/roles", json={"name": "bad", "permissions": ["fly"]}).status_code == 422
    new = {"full_name": "Rae", "email": "rae@zephyr.test", "password": PW, "roles": ["lead", "reviewer"],
           "membership": {"team_id": research, "is_lead": True}}
    assert ceo.post("/api/admin/users", json={**new, "password": "short"}).status_code == 422
    assert ceo.post("/api/admin/users", json={**new, "roles": ["wizard"]}).status_code == 422
    assert ceo.post("/api/admin/users", json={**new, "membership": {"team_id": ids["org"]}}).status_code == 422   # an id from elsewhere is not a team here
    assert ceo.post("/api/admin/users", json={**new, "membership": {"team_id": research, "reports_to": ids["Trevor"]}}).status_code == 422
    assert ceo.post("/api/admin/users", json=new).status_code == 201
    assert ceo.post("/api/admin/users", json={**new, "full_name": "Rae 2"}).status_code == 422          # duplicate email
    rae = login("rae@zephyr.test")
    code = lambda n: rae.get(f"/api/people/{zep['u'][n]}").status_code
    assert code("Dev") == 200 and rae.get("/api/org/overview").status_code == 200                     # permission matrix, not role names
    assert rae.get("/api/settings").status_code == 404
    rid = next(r["id"] for r in ceo.get("/api/admin/roles").get_json()["roles"] if r["name"] == "reviewer")
    assert ceo.put(f"/api/admin/roles/{rid}", json={"permissions": []}).status_code == 200
    assert code("Dev") == 404                                                                          # same login, next request
    homerr = login("ceo@homerr.test")
    assert "reviewer" not in [r["name"] for r in homerr.get("/api/admin/roles").get_json()["roles"]]
    assert homerr.patch(f"/api/admin/users/{zep['u']['Dev']}", json={"active": False}).status_code == 404
    rae_id = next(p["id"] for p in ceo.get("/api/org/overview").get_json()["people"] if p["name"] == "Rae")
    assert ceo.patch(f"/api/admin/users/{rae_id}", json={"active": False}).status_code == 200
    assert rae.get("/api/people").status_code == 401                                                   # deactivated users are locked out at once

def test_homerr_is_seeded_from_data_only(ids):
    spec = json.loads((pathlib.Path(__file__).resolve().parents[1] / "seeds/homerr.json").read_text())
    spec["organisation"] = {"name": "Homerr (seed check)", "slug": "homerr-seed-check"}
    with db_cursor() as cur:
        org = seed_org(cur, spec, "ceo@seedcheck.test", PW)
        s = svc.effective_settings(cur, org)
        cur.execute("SELECT t.name, u.full_name, m.is_lead, r.full_name AS boss FROM team_memberships m JOIN teams t ON t.id=m.team_id "
                    "JOIN users u ON u.id=m.user_id LEFT JOIN users r ON r.id=m.reports_to WHERE m.org_id=%s ORDER BY t.name, m.is_lead DESC", (org,))
        rows = [tuple(r.values()) for r in cur.fetchall()]
    assert s["point_rubric"]["3"] == "About one day" and s["point_labels"]["8"] == "L" and s["point_scale"] == [1, 2, 3, 5, 8]
    assert rows == [("Finance and Strategy", "Michael", True, None), ("Marketing", "Laura", True, None),
                    ("Operations", "Shenaaz (Naaz)", True, None), ("Tech", "Trevor", True, None), ("Tech", "Georgina", False, "Trevor")]
