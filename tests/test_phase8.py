import pytest
from app import create_app
from app.db import db_cursor
from seed import seed_org
from tests.conftest import PW

SPEC = {"organisation": {"name": "Acme Admin", "slug": "acme-admin"}, "ceo": {"name": "Ada"},
        "teams": [{"name": "Alpha", "lead": "Abe", "members": [{"name": "Bea", "reports_to": "Abe"}]},
                  {"name": "Beta", "lead": "Cy", "members": []}]}

@pytest.fixture(scope="module")
def acme(ids):
    with db_cursor() as cur:
        org = seed_org(cur, SPEC, "ada@acme.test", PW)
        cur.execute("SELECT id, name FROM teams WHERE org_id=%s", (org,)); t = {r["name"]: r["id"] for r in cur.fetchall()}
    return {"org": org, "t": t}

def test_people_screen_data_and_access(acme, login):
    ceo = login("ada@acme.test")
    d = ceo.get("/api/admin/people").get_json()
    assert [p["name"] for p in d["people"]] == ["Abe", "Ada", "Bea", "Cy"]
    by = {p["name"]: p for p in d["people"]}
    assert by["Bea"]["teams"] == [{"team_id": acme["t"]["Alpha"], "team": "Alpha", "is_lead": False, "reports_to": "Abe"}]
    assert by["Bea"]["roles"] == ["member"] and by["Ada"]["roles"] == ["admin", "ceo"]
    assert by["Abe"]["has_password"] is False and by["Ada"]["has_password"] is True
    assert "password_hash" not in str(d) and [t["name"] for t in d["teams"]] == ["Alpha", "Beta"] and "member" in d["roles"]
    assert "Add a person" in ceo.get("/admin/people").get_data(as_text=True)
    # the lead gets her login through the screen's endpoints, and then has no access to the screen itself
    assert ceo.patch(f"/api/admin/users/{by['Abe']['id']}", json={"email": "abe@acme.test", "password": PW}).status_code == 200
    abe = login("abe@acme.test")
    assert abe.get("/admin/people").status_code == 404 and abe.get("/api/admin/people").status_code == 404
    assert any(t == "Alpha" for t in [x["team"] for x in ceo.get("/api/admin/people").get_json()["people"][0]["teams"]])

def test_people_screen_escapes_names_and_is_isolated(acme, login, ids):
    ceo = login("ada@acme.test")
    r = ceo.post("/api/admin/users", json={"full_name": "<b>x</b> Eve", "membership": {"team_id": acme["t"]["Beta"]}})
    assert r.status_code == 201
    html = ceo.get("/admin/people").get_data(as_text=True)
    assert "&lt;b&gt;x&lt;/b&gt; Eve" in html and "<b>x</b>" not in html
    homerr = login("ceo@homerr.test").get("/api/admin/people").get_json()
    names = [p["name"] for p in homerr["people"]]
    assert "Georgina" in names and "Abe" not in names and [t["name"] for t in homerr["teams"]] == ["Marketing", "Tech"]

def test_new_person_can_sign_in_then_be_switched_off(acme, login):
    ceo = login("ada@acme.test")
    r = ceo.post("/api/admin/users", json={"full_name": "Eli", "email": "eli@acme.test", "password": PW,
                                           "membership": {"team_id": acme["t"]["Beta"]}})
    eid = r.get_json()["id"]
    eli = login("eli@acme.test")
    assert eli.get("/api/people").status_code == 200
    assert ceo.patch(f"/api/admin/users/{eid}", json={"active": False}).status_code == 200
    assert eli.get("/api/people").status_code == 401
    row = next(p for p in ceo.get("/api/admin/people").get_json()["people"] if p["id"] == eid)
    assert row["active"] is False

def test_health_headers_and_real_client_ip_for_rate_limits(app, monkeypatch):
    c = app.test_client()
    r = c.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    monkeypatch.setenv("PROXY_HOPS", "1")                                   # behind Render's proxy
    live = create_app(testing=False).test_client()
    post = lambda ip: live.post("/auth/login", json={"email": "x@y.z", "password": "bad"}, headers={"X-Forwarded-For": ip}).status_code
    assert [post("9.9.9.9") for _ in range(6)] == [401] * 5 + [429]         # five tries a minute from one visitor
    assert post("8.8.8.8") == 401                                           # a different visitor is not locked out with them
