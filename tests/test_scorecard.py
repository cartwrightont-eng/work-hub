"""Phase 3 acceptance: hand-calculated sample data must match the dashboard exactly.
Person: Mia. As of Sunday 2026-11-22, five weeks shown, timezone Africa/Nairobi (UTC+3).
Weeks (Mon-Sun): W43 Oct19, W44 Oct26, W45 Nov2, W46 Nov9, W47 Nov16.

  id  pts deadline   started(latest)  done (UTC)         notes
  A   3   Nov 20     Nov 17 06:00     Nov 19 12:00       on time, cycle 2.25 d
  B   2   Nov 18     Nov 12 06:00     Nov 19 06:00       late, cycle 7.0 d
  C   5   Nov 14     Nov 13 06:00     Nov 14 06:00       on time, reopened once; clock restarts, cycle 1.0 d
  D   1   Nov 15     Nov 14 21:30     Nov 15 21:30       done 00:30 Mon Nov 16 Nairobi: LATE and counted in W47
  E   8   Nov 6      Oct 28 06:00     Nov 5 06:00        on time, cycle 8.0 d
  F   3   Nov 27     Nov 20           not done           not yet due: excluded from on-time
  G   2   Nov 20     Nov 18           not done           overdue: counts as late
  H   2   Oct 30     Oct 28 06:00     Oct 29 06:00       on time, cycle 1.0 d
"""
import pytest
from pytest import approx
from app.db import db_cursor
from seed import seed_org
from tests.conftest import PW

SPEC = {"organisation": {"name": "Metrics Co", "slug": "metrics"}, "ceo": {"name": "Casey"},
        "teams": [{"name": "Core", "lead": "Liam", "members": [{"name": "Mia", "reports_to": "Liam"}]}]}
B, P, R, I, V = "backlog", "in_progress", "in_review", "reopened", "done"
DATA = [
 ("Fix <b>x</b> upload", 3, "2026-11-20", V, [(None,B,"11-16 06:00"),(B,P,"11-17 06:00"),(P,R,"11-19 06:00"),(R,V,"11-19 12:00")]),
 ("B", 2, "2026-11-18", V, [(None,B,"11-10 06:00"),(B,P,"11-12 06:00"),(P,R,"11-17 06:00"),(R,V,"11-19 06:00")]),
 ("C", 5, "2026-11-14", V, [(None,B,"11-08 06:00"),(B,P,"11-09 06:00"),(P,R,"11-12 06:00"),(R,I,"11-12 12:00"),
                            (I,P,"11-13 06:00"),(P,R,"11-13 12:00"),(R,V,"11-14 06:00")]),
 ("D", 1, "2026-11-15", V, [(None,B,"11-14 06:00"),(B,P,"11-14 21:30"),(P,R,"11-15 12:00"),(R,V,"11-15 21:30")]),
 ("E", 8, "2026-11-06", V, [(None,B,"10-27 06:00"),(B,P,"10-28 06:00"),(P,R,"11-04 06:00"),(R,V,"11-05 06:00")]),
 ("F", 3, "2026-11-27", P, [(None,B,"11-18 06:00"),(B,P,"11-20 06:00")]),
 ("G", 2, "2026-11-20", P, [(None,B,"11-17 06:00"),(B,P,"11-18 06:00")]),
 ("H", 2, "2026-10-30", V, [(None,B,"10-27 06:00"),(B,P,"10-28 06:00"),(P,R,"10-28 12:00"),(R,V,"10-29 06:00")]),
]

@pytest.fixture(scope="module")
def mia(ids):
    with db_cursor() as cur:
        org = seed_org(cur, SPEC, "ceo@metrics.test", PW)
        cur.execute("SELECT id, full_name, 1 FROM users WHERE org_id=%s", (org,))
        u = {r["full_name"]: r["id"] for r in cur.fetchall()}
        cur.execute("SELECT id FROM teams WHERE org_id=%s", (org,)); team = cur.fetchone()["id"]
        for title, pts, dl, status, evs in DATA:
            cur.execute("INSERT INTO deliverables (org_id,team_id,owner_id,created_by,title,points,original_deadline,deadline,status) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (org, team, u["Mia"], u["Liam"], title, pts, dl, dl, status))
            did = cur.fetchone()["id"]
            for frm, to, ts in evs:
                cur.execute("INSERT INTO status_events (org_id,deliverable_id,from_status,to_status,actor_id,created_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", (org, did, frm, to, u["Liam"], f"2026-{ts}+00"))
    return u["Mia"]

def card(login, mia):
    return login("ceo@metrics.test").get(f"/api/people/{mia}/scorecard?as_of=2026-11-22&weeks=5").get_json()

def test_series_match_hand_calculation(login, mia):
    s = card(login, mia)
    assert [x["week"] for x in s["on_time"]] == ["2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]
    assert [x["rate"] for x in s["on_time"]] == [None, 1.0, 1.0, 0.5, approx(0.3333, abs=1e-4)]
    assert [x["n"] for x in s["on_time"]] == [0, 1, 1, 2, 3]
    assert [x["points"] for x in s["work_rate"]] == [0, 2, 8, 5, 6]
    assert [x["tasks"] for x in s["work_rate"]] == [0, 1, 1, 1, 3]
    assert [x["rolling"] for x in s["work_rate"]] == [None, None, None, 3.75, 5.25]
    assert [x["median_days"] for x in s["cycle_time"]] == [None, 1.0, 8.0, 1.0, 2.25]
    assert [x["n"] for x in s["cycle_time"]] == [0, 1, 1, 1, 3]
    assert [(x["month"], x["n"], x["reopened"]) for x in s["rework"]] == [("2026-10", 1, 0), ("2026-11", 5, 1)]
    assert [x["rate"] for x in s["rework"]] == [0.0, 0.2]

def test_tiles_match_hand_calculation(login, mia):
    t, p = card(login, mia)["tiles"], card(login, mia)["previous"]
    assert t["on_time"] == {"rate": approx(0.5714, abs=1e-4), "n": 7}
    assert t["work_rate"] == {"points_per_week": 5.25, "tasks_per_week": 1.5, "n": 6}
    assert t["cycle_time"] == {"median_days": 1.625, "n": 6}
    assert t["rework"] == {"rate": approx(0.1667, abs=1e-4), "n": 6}
    assert p["on_time"]["rate"] is None and p["work_rate"]["n"] == 0     # no earlier data, no invented trend

def test_drilldown_ids_point_at_the_deliverables_behind_a_score(login, mia):
    s = card(login, mia)
    ids = s["on_time"][4]["ids"]                                           # W47: A, B, G
    c = login("ceo@metrics.test")
    assert sorted(c.get(f"/api/deliverables/{i}").get_json()["title"] for i in ids) == ["B", "Fix <b>x</b> upload", "G"]
    c_events = c.get(f"/api/deliverables/{s['rework'][1]['ids'][0]}").get_json()["events"]
    assert c_events and c_events[0]["to_status"] == "backlog"

def test_scorecard_scope_and_audit(login, ids):
    with db_cursor() as cur: cur.execute("DELETE FROM audit_log")
    G = ids["Georgina"]
    for who in ("georgina@x.test", "trevor@x.test", "ceo@homerr.test"):
        r = login(who).get(f"/api/people/{G}/scorecard")
        assert r.status_code == 200
    laura = login("ceo@homerr.test").get(f"/api/people/{ids['Laura']}/scorecard").get_json()     # Laura never has deliverables
    assert laura["tiles"]["on_time"]["rate"] is None and laura["tiles"]["work_rate"]["n"] == 0    # empty history: no data, no error
    for who in ("peter@x.test", "laura@x.test", "ceo@other.test"):
        assert login(who).get(f"/api/people/{G}/scorecard").status_code == 404
    assert login("georgina@x.test").get(f"/api/people/{ids['Trevor']}/scorecard").status_code == 404
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT actor_id FROM audit_log WHERE action='view_scorecard' AND target_user_id=%s ORDER BY id", (G,))
        assert [r["actor_id"] for r in cur.fetchall()] == [ids["Trevor"], ids["Sebastian Rague"]]

def test_dashboard_page(login, mia, app):
    c = login("ceo@metrics.test")
    html = c.get(f"/people/{mia}").get_data(as_text=True)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html       # stored XSS escaped
    assert "input to a review conversation" in html and "Rank" not in html
    anon = app.test_client()
    assert anon.get("/my-work").headers["Location"].endswith("/login")
    assert anon.get("/login").status_code == 200
    assert login("peter@x.test").get(f"/people/{mia}").status_code == 404

def test_team_and_org_rows_use_own_previous_period(login, mia):
    """As of 2026-12-06, 4 weeks: current window Nov 9-Dec 6, previous Oct 12-Nov 8.
    On-time now: C, A ok; D, B, G, F late -> 2/6. Before: E, H both ok -> 2/2. Change = (0.3333-1.0)*100 = -66.7.
    Rework now: A, B, C, D completed, C reopened -> 1/4 = 0.25. Before: E, H -> 0/2. Change = +25.0."""
    c = login("ceo@metrics.test")
    org = c.get("/api/org/overview?as_of=2026-12-06&weeks=4").get_json()
    mia_row = next(p for p in org["people"] if p["name"] == "Mia")
    assert mia_row["on_time"] == {"rate": approx(0.3333, abs=1e-4), "n": 6, "change_pts": -66.7}
    assert mia_row["rework"] == {"rate": 0.25, "n": 4, "change_pts": 25.0}
    assert [p["name"] for p in org["people"]] == ["Casey", "Liam", "Mia"]
    assert org["teams"][0]["on_time"] == mia_row["on_time"]                    # Core's only data is Mia's
    team = c.get(f"/api/teams/{org['teams'][0]['id']}/overview?as_of=2026-12-06&weeks=4").get_json()
    assert [p["name"] for p in team["people"]] == ["Liam", "Mia"] and team["summary"]["rework"]["n"] == 4
