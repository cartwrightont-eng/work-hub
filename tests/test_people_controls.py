"""People screen: move someone between teams and make them a lead, for people who joined with the join code."""
import pytest
from app import admin_service as svc
from app.db import db_cursor
from tests.conftest import PW

@pytest.fixture(scope="module")
def jo(ids):
    with db_cursor() as cur:
        org = svc.create_org(cur, "Join Co", "join-co")
        ceo = svc.create_user(cur, org, "Jo Boss", "boss@join.test", PW)
        svc.set_roles(cur, org, ceo, ["ceo", "admin"])
        t = {n: svc.create_team(cur, org, n) for n in ("Alpha", "Beta")}
    return {"org": org, "t": t}

def join(app, jo, code, name, email, team):
    r = app.test_client().post("/auth/signup", json={"org": "Join Co", "code": code, "full_name": name, "email": email, "password": PW, "team_id": jo["t"][team]})
    assert r.status_code == 200, r.get_json()
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,)); return cur.fetchone()["id"]

def boss_of(uid):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT team_id, is_lead, reports_to FROM team_memberships WHERE user_id=%s ORDER BY team_id", (uid,)); return cur.fetchall()

def test_a_joiner_becomes_a_lead_and_people_move_teams(app, jo, login):
    ceo = login("boss@join.test")
    code = ceo.get("/api/join-code").get_json()["code"]
    lena, gus = (join(app, jo, code, n, e, "Alpha") for n, e in (("Lena Lee", "lena@join.test"), ("Gus Gray", "gus@join.test")))
    L, G = login("lena@join.test"), login("gus@join.test")
    alpha, beta = jo["t"]["Alpha"], jo["t"]["Beta"]
    assert L.get(f"/team/{alpha}").status_code == 404 and L.get(f"/api/people/{gus}").status_code == 404     # a joiner is only a member
    assert ceo.patch(f"/api/admin/users/{lena}", json={"membership": {"team_id": alpha, "is_lead": True}, "roles": ["lead"]}).status_code == 200
    assert L.get(f"/team/{alpha}").status_code == 200 and L.get(f"/api/people/{gus}").status_code == 200       # same login, next request
    assert G.get(f"/api/people/{lena}").status_code == 404                                                      # members never see the lead's page
    assert boss_of(lena) == [{"team_id": alpha, "is_lead": True, "reports_to": None}]
    hal = join(app, jo, code, "Hal Hill", "hal@join.test", "Alpha")
    assert boss_of(hal)[0]["reports_to"] == lena                                                                # joiners now report to the new lead
    assert ceo.patch(f"/api/admin/users/{hal}", json={"membership": {"team_id": alpha, "is_lead": False}}).status_code == 200
    assert boss_of(hal)[0]["reports_to"] == lena                                                                # omitted means unchanged
    assert ceo.patch(f"/api/admin/users/{gus}", json={"membership": {"team_id": beta, "is_lead": False}, "leave_team_id": alpha, "roles": ["member"]}).status_code == 200
    assert [m["team_id"] for m in boss_of(gus)] == [beta] and L.get(f"/api/people/{gus}").status_code == 404   # moved out of Lena's team
    assert ceo.patch(f"/api/admin/users/{lena}", json={"membership": {"team_id": alpha, "is_lead": False}, "roles": ["member"]}).status_code == 200
    assert L.get(f"/team/{alpha}").status_code == 404                                                           # and demoting works too

def test_people_screen_has_the_controls_and_stays_admin_only(app, jo, login, ids):
    ceo = login("boss@join.test")
    html = ceo.get("/admin/people").get_data(as_text=True)
    assert "Leads this team" in html and "Team and lead" in html and 'data-roles=' in html
    assert login("lena@join.test").get("/admin/people").status_code == 404
    other_team = next(iter(login("ceo@homerr.test").get("/api/admin/people").get_json()["teams"]))["id"]
    me = next(p["id"] for p in ceo.get("/api/admin/people").get_json()["people"] if p["name"] == "Hal Hill")
    assert ceo.patch(f"/api/admin/users/{me}", json={"membership": {"team_id": other_team, "is_lead": True}}).status_code == 422   # another organisation's team
