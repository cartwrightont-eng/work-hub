import pytest
from app.db import db_cursor
from tests.conftest import PW

def code(c, i): return c.get(f"/api/people/{i}").status_code
def names(c): return [p["full_name"] for p in c.get("/api/people").get_json()["people"]]

def test_member_sees_only_self(login, ids):
    c = login("georgina@x.test")
    assert code(c, ids["Georgina"]) == 200
    for other in ("Peter", "Trevor", "Laura", "Sebastian Rague"):
        assert code(c, ids[other]) == 404
    assert names(c) == ["Georgina"]

def test_peers_cannot_see_each_other(login, ids):
    assert code(login("peter@x.test"), ids["Georgina"]) == 404
    assert code(login("georgina@x.test"), ids["Peter"]) == 404

def test_lead_sees_own_team_only(login, ids):
    c = login("trevor@x.test")
    assert code(c, ids["Georgina"]) == 200 and code(c, ids["Peter"]) == 200
    assert code(c, ids["Trevor"]) == 200
    assert code(c, ids["Laura"]) == 404              # other team's lead
    assert code(c, ids["Sebastian Rague"]) == 404
    assert names(c) == ["Georgina", "Peter", "Trevor"]

def test_leads_do_not_see_each_other(login, ids):
    assert code(login("laura@x.test"), ids["Trevor"]) == 404
    assert code(login("trevor@x.test"), ids["Laura"]) == 404

def test_subordinate_cannot_see_lead(login, ids):
    assert code(login("georgina@x.test"), ids["Trevor"]) == 404

def test_ceo_sees_everyone_in_org_only(login, ids):
    c = login("ceo@homerr.test")
    for n in ("Georgina", "Peter", "Trevor", "Laura", "Sebastian Rague"):
        assert code(c, ids[n]) == 200
    assert code(c, ids["Sam"]) == 404 and code(c, ids["Olive"]) == 404
    assert names(c) == sorted(["Georgina", "Peter", "Trevor", "Laura", "Sebastian Rague"])

def test_other_org_cannot_reach_homerr(login, ids):
    c = login("ceo@other.test")
    assert code(c, ids["Georgina"]) == 404 and code(c, ids["Sebastian Rague"]) == 404
    assert code(login("sam@x.test"), ids["Laura"]) == 404

def test_team_change_applies_on_next_request(login, ids):
    c = login("trevor@x.test")                       # same token throughout
    assert code(c, ids["Peter"]) == 200
    with db_cursor() as cur:
        cur.execute("UPDATE team_memberships SET reports_to=NULL, team_id=(SELECT id FROM teams WHERE name='Marketing' AND org_id=%s) "
                    "WHERE user_id=%s", (ids["org"], ids["Peter"]))
    assert code(c, ids["Peter"]) == 404
    with db_cursor() as cur:                         # restore for other tests
        cur.execute("UPDATE team_memberships SET reports_to=%s, team_id=(SELECT id FROM teams WHERE name='Tech' AND org_id=%s) "
                    "WHERE user_id=%s", (ids["Trevor"], ids["org"], ids["Peter"]))

def test_viewing_others_is_audited_self_is_not(login, ids):
    with db_cursor() as cur:
        cur.execute("DELETE FROM audit_log")
    c = login("trevor@x.test")
    code(c, ids["Trevor"]); code(c, ids["Georgina"])
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT actor_id, target_user_id, action FROM audit_log")
        rows = cur.fetchall()
    assert rows == [{"actor_id": ids["Trevor"], "target_user_id": ids["Georgina"], "action": "view_scorecard"}]

def test_requires_login(app, ids):
    assert app.test_client().get(f"/api/people/{ids['Georgina']}").status_code == 401

def test_bad_login_and_csrf(app, login):
    assert app.test_client().post("/auth/login", json={"email": "peter@x.test", "password": "nope"}).status_code == 401
    c = login("peter@x.test", csrf=False)
    assert c.post("/auth/logout").status_code == 401
    assert login("peter@x.test").post("/auth/logout").status_code == 200   # cookie present, CSRF header missing

def test_password_rules():
    from app.security import password_problems
    assert password_problems("short1A") and password_problems("alllowercase123") == ["an uppercase letter"]
    assert password_problems(PW) == []
