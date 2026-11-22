"""Phase 4 acceptance: a lead cannot reach another team's data by any URL."""
import pytest
from app.db import db_cursor
from tests.test_deliverables import mk

@pytest.fixture
def teams(ids):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT name, id FROM teams WHERE org_id=%s", (ids["org"],))
        return {r["name"]: r["id"] for r in cur.fetchall()}

def sweep(c, person_ids, team_id, did):
    """Every route that takes a person, team or deliverable id, aimed at someone else's data."""
    gets = [f"{p}{i}{s}" for i in person_ids for p, s in [("/people/", ""), ("/api/people/", ""), ("/api/people/", "/scorecard")]]
    gets += [f"/team/{team_id}", f"/api/teams/{team_id}/overview", "/org", "/api/org/overview",
             f"/api/deliverables/{did}", f"/api/deliverables/{did}/worklog"]
    codes = {u: c.get(u).status_code for u in gets}
    codes["POST transition"] = c.post(f"/api/deliverables/{did}/transition", json={"action": "start"}).status_code
    codes["PATCH"] = c.patch(f"/api/deliverables/{did}", json={"deadline": "2030-01-01", "reason": "x"}).status_code
    codes["POST worklog"] = c.post(f"/api/deliverables/{did}/worklog", json={"body": "x"}).status_code
    return {k: v for k, v in codes.items() if v != 404}      # anything left is a leak

def test_other_team_lead_cannot_reach_tech_by_any_url(login, ids, teams):
    did = mk(login("trevor@x.test"), ids).get_json()["id"]
    laura = login("laura@x.test")
    leaks = sweep(laura, [ids["Georgina"], ids["Peter"], ids["Trevor"]], teams["Tech"], did)
    assert leaks == {}, leaks
    assert laura.post("/api/deliverables", json={"title": "x", "owner_id": ids["Georgina"], "team_id": teams["Tech"],
                                                  "deadline": "2026-12-01"}).status_code == 403
    assert did not in [d["id"] for d in laura.get("/api/deliverables").get_json()["deliverables"]]
    own = laura.get(f"/api/teams/{teams['Marketing']}/overview")
    assert own.status_code == 200 and [p["name"] for p in own.get_json()["people"]] == ["Laura"]

def test_member_and_peer_cannot_reach_it_either(login, ids, teams):
    did = mk(login("trevor@x.test"), ids).get_json()["id"]            # Georgina's deliverable
    assert sweep(login("peter@x.test"), [ids["Georgina"], ids["Trevor"]], teams["Tech"], did) == {}
    other = login("ceo@other.test")
    leaks = sweep(other, [ids["Georgina"], ids["Trevor"], ids["Laura"]], teams["Tech"], did)
    assert leaks == {"/org": 200, "/api/org/overview": 200}     # their OWN organisation's overview is allowed...
    names = [p["name"] for p in other.get("/api/org/overview").get_json()["people"]]
    assert names == ["Olive", "Sam"]                            # ...and contains none of Homerr's people

def test_lead_team_page_rules(login, ids, teams):
    c = login("trevor@x.test")
    r = c.get(f"/api/teams/{teams['Tech']}/overview")
    d = r.get_json()
    assert [p["name"] for p in d["people"]] == ["Georgina", "Peter", "Trevor"]           # alphabetical
    assert all(set(p) == {"id", "name", "role", "on_time", "rework"} for p in d["people"])  # no points in cross-person rows
    assert len(d["charts"]) == 3 and all(len(ch["points"]) == len(ch["labels"]) for ch in d["charts"])
    assert c.get(f"/team/{teams['Tech']}").status_code == 200
    assert c.get("/api/org/overview").status_code == 404 and c.get("/org").status_code == 404

def test_ceo_org_view_period_filter_and_audit(login, ids, teams):
    with db_cursor() as cur: cur.execute("DELETE FROM audit_log")
    c = login("ceo@homerr.test")
    d = c.get("/api/org/overview?weeks=8").get_json()
    assert d["period_weeks"] == 8 and [t["name"] for t in d["teams"]] == ["Marketing", "Tech"]
    assert [p["name"] for p in d["people"]] == sorted(p["name"] for p in d["people"])
    assert c.get("/api/org/overview?weeks=999").get_json()["period_weeks"] == 4          # unknown period falls back
    assert c.get("/org").status_code == 200 and c.get(f"/team/{teams['Tech']}?weeks=12").status_code == 200
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT count(*) AS n FROM audit_log WHERE action='view_scorecard' AND actor_id=%s", (ids["Sebastian Rague"],))
        assert cur.fetchone()["n"] > 0                                                    # other people's views are logged
