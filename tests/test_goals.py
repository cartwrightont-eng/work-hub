"""Phase 5 acceptance: changing a personal key result updates team and company progress.
Hand calculation (cycle Oct 1 - Dec 31 = 92 days; as of Nov 15 that is 46/92 = 0.5 elapsed):
  Personal P1 (Georgina): KR 1 of 4 = 0.25, KR 5 of 10 = 0.5         -> mean 0.375
  Personal P2 (Peter):    KR 2 of 8 = 0.25                            -> 0.25
  Team T (Tech):          own KR 5 of 10 = 0.5, plus P1 and P2        -> (0.5 + 0.375 + 0.25) / 3 = 0.375
  Company C:              own KR 40 of 100 = 0.4, plus T              -> (0.4 + 0.375) / 2 = 0.3875
  Team T2 (Marketing): no key results -> no data, and left out of the company average.
Georgina then moves her first KR from 1 to 4 (= 1.0):
  P1 = (1.0 + 0.5) / 2 = 0.75;  T = (0.5 + 0.75 + 0.25) / 3 = 0.5;  C = (0.4 + 0.5) / 2 = 0.45
Status = gap between elapsed (0.5) and progress: <= 0.10 on track, <= 0.25 at risk, else off track."""
import pytest
from pytest import approx
from app.db import db_cursor
from app.goals_calc import kr_progress, elapsed_share, status_for
from datetime import date
from tests.test_deliverables import mk

CYC = {"cycle_start": "2026-10-01", "cycle_end": "2026-12-31"}
ASOF = "?as_of=2026-11-15"

def post(c, url, **j):
    r = c.post(url, json=j)
    assert r.status_code == 201, (url, r.status_code, r.get_json())
    return r.get_json()["id"]

@pytest.fixture
def g(login, ids):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT name, id FROM teams WHERE org_id=%s", (ids["org"],))
        teams = {r["name"]: r["id"] for r in cur.fetchall()}
    who = {n: login(e) for n, e in [("ceo", "ceo@homerr.test"), ("trevor", "trevor@x.test"), ("laura", "laura@x.test"),
                                   ("georgina", "georgina@x.test"), ("peter", "peter@x.test"), ("other", "ceo@other.test")]}
    s = dict(who=who, ids=ids, teams=teams)
    s["C"] = post(who["ceo"], "/api/objectives", level=1, title="Grow verified listings", **CYC)
    s["Ck"] = post(who["ceo"], f"/api/objectives/{s['C']}/key-results", title="Verified listings", target=100, current=40)
    s["T"] = post(who["trevor"], "/api/objectives", level=2, title="Ship faster", parent_id=s["C"], team_id=teams["Tech"], **CYC)
    s["Tk"] = post(who["trevor"], f"/api/objectives/{s['T']}/key-results", title="Releases", target=10, current=5)
    s["P1"] = post(who["georgina"], "/api/objectives", level=3, title="Fix upload", parent_id=s["T"], **CYC)
    s["P1a"] = post(who["georgina"], f"/api/objectives/{s['P1']}/key-results", title="Bugs closed", target=4, current=1)
    s["P1b"] = post(who["georgina"], f"/api/objectives/{s['P1']}/key-results", title="Tests added", target=10, current=5)
    s["P2"] = post(who["peter"], "/api/objectives", level=3, title="Peter private goal", parent_id=s["T"], **CYC)
    s["P2a"] = post(who["peter"], f"/api/objectives/{s['P2']}/key-results", title="Peter KR", target=8, current=2)
    s["T2"] = post(who["laura"], "/api/objectives", level=2, title="Brand", parent_id=s["C"], team_id=teams["Marketing"], **CYC)
    return s

def prog(g, who, key):
    r = g["who"][who].get(f"/api/objectives/{g[key]}{ASOF}").get_json()
    return r["progress"], r["status"]

def test_rollup_matches_hand_calculation_and_update_propagates(g):
    assert prog(g, "ceo", "P1") == (0.375, "at_risk")
    assert prog(g, "ceo", "P2") == (0.25, "at_risk")
    assert prog(g, "ceo", "T") == (0.375, "at_risk")
    assert prog(g, "ceo", "C") == (0.3875, "at_risk")
    assert prog(g, "ceo", "T2") == (None, "no_data")
    assert g["who"]["ceo"].get(f"/api/objectives/{g['C']}{ASOF}").get_json()["elapsed"] == 0.5
    r = g["who"]["georgina"].patch(f"/api/key-results/{g['P1a']}", json={"current": 4, "note": "closed the last bug"})
    assert r.status_code == 200 and r.get_json()["current"] == 4
    assert prog(g, "ceo", "P1") == (0.75, "on_track")
    assert prog(g, "ceo", "T") == (0.5, "on_track")
    assert prog(g, "ceo", "C") == (0.45, "on_track")
    assert prog(g, "ceo", "P2") == (0.25, "at_risk")                       # untouched
    assert prog(g, "trevor", "C") == (0.45, "on_track")                    # everyone sees the same company number
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT old_value, new_value, actor_id, note FROM kr_updates WHERE key_result_id=%s", (g["P1a"],))
        row = cur.fetchone()
    assert (float(row["old_value"]), float(row["new_value"]), row["actor_id"], row["note"]) == (1.0, 4.0, g["ids"]["Georgina"], "closed the last bug")

def test_status_and_progress_rules():
    assert status_for(0.45, 0.5) == "on_track" and status_for(0.375, 0.5) == "at_risk" and status_for(0.1, 0.5) == "off_track"
    assert status_for(None, 0.5) == "no_data"
    assert kr_progress(0, 10, 15) == 1.0 and kr_progress(0, 10, -3) == 0.0 and kr_progress(20, 10, 15) == 0.5   # clamped; downward targets work
    assert elapsed_share(date(2026, 10, 1), date(2026, 12, 31), date(2026, 9, 1)) == 0.0
    assert elapsed_share(date(2026, 10, 1), date(2026, 12, 31), date(2027, 2, 1)) == 1.0

def test_who_can_create_and_edit(g):
    w, t = g["who"], g["teams"]
    mk_obj = lambda c, **j: c.post("/api/objectives", json={**CYC, **j}).status_code
    assert mk_obj(w["georgina"], level=1, title="x") == 403
    assert mk_obj(w["georgina"], level=2, title="x", parent_id=g["C"], team_id=t["Tech"]) == 403
    assert mk_obj(w["laura"], level=2, title="x", parent_id=g["C"], team_id=t["Tech"]) == 403
    assert mk_obj(w["georgina"], level=3, title="x", parent_id=g["T"], owner_id=g["ids"]["Peter"]) == 403
    assert mk_obj(w["georgina"], level=3, title="x", parent_id=g["T2"]) == 422        # parent belongs to another team
    assert mk_obj(w["trevor"], level=2, title="x", team_id=t["Tech"]) == 422          # must link to a company goal
    assert mk_obj(w["trevor"], level=2, title="x", parent_id=g["P1"], team_id=t["Tech"]) == 422
    assert mk_obj(w["trevor"], level=3, title="for Georgina", parent_id=g["T"], owner_id=g["ids"]["Georgina"]) == 201   # lead sets it with her
    kr = lambda c, kid, **j: c.patch(f"/api/key-results/{kid}", json=j).status_code
    assert kr(w["georgina"], g["Tk"], current=6) == 403            # team KR: members read only
    assert kr(w["peter"], g["P1a"], current=2) == 404              # peer's personal KR is invisible
    assert kr(w["laura"], g["P1a"], current=2) == 404
    assert kr(w["trevor"], g["P1a"], current=2) == 200 and kr(w["ceo"], g["P1a"], current=3) == 200
    assert kr(w["georgina"], g["P1a"], current="lots") == 422
    assert w["georgina"].post(f"/api/objectives/{g['T']}/key-results", json={"title": "x", "target": 5}).status_code == 403
    assert w["georgina"].post(f"/api/objectives/{g['P1']}/key-results", json={"title": "x", "target": 0, "start_value": 0}).status_code == 422

def test_visibility(g):
    def seen(who):
        return {o["id"] for o in g["who"][who].get("/api/objectives").get_json()["objectives"]}
    mine = {k: g[k] for k in ("C", "T", "P1", "P2", "T2")}
    pick = lambda s: {k for k, v in mine.items() if v in s}
    assert pick(seen("georgina")) == {"C", "T", "P1"}
    assert pick(seen("peter")) == {"C", "T", "P2"}
    assert pick(seen("laura")) == {"C", "T2"}
    assert pick(seen("trevor")) == {"C", "T", "P1", "P2"}
    assert pick(seen("ceo")) == {"C", "T", "P1", "P2", "T2"}
    assert pick(seen("other")) == set()
    assert g["who"]["georgina"].get(f"/api/objectives/{g['P2']}").status_code == 404
    assert g["who"]["other"].get(f"/api/objectives/{g['C']}").status_code == 404
    # team progress is an aggregate, so Georgina sees it, but not Peter's goal inside it
    assert g["who"]["georgina"].get(f"/api/objectives/{g['T']}{ASOF}").get_json()["progress"] == 0.375

def test_link_deliverable_to_key_result(g):
    w, G = g["who"], g["ids"]["Georgina"]
    did = mk(w["trevor"], g["ids"]).get_json()["id"]
    link = lambda c, kid: c.post(f"/api/deliverables/{did}/link", json={"key_result_id": kid}).status_code
    assert link(w["georgina"], g["P2a"]) == 404                    # cannot see Peter's goal
    assert link(w["trevor"], g["P2a"]) == 422                      # visible to the lead, but belongs to someone else
    assert link(w["peter"], g["P1a"]) == 404                       # cannot see the deliverable at all
    assert link(w["georgina"], g["P1a"]) == 200
    assert [d["id"] for d in w["georgina"].get(f"/api/objectives/{g['P1']}").get_json()["deliverables"]] == [did]
    assert link(w["georgina"], g["Ck"]) == 200                     # company key results can be linked to
    assert [d["id"] for d in w["ceo"].get(f"/api/objectives/{g['C']}").get_json()["deliverables"]] == [did]

def test_weekly_checkin_prompt(g):
    with db_cursor() as cur:
        cur.execute("UPDATE key_results SET updated_at = now() - interval '10 days' WHERE id=%s", (g["P1a"],))
    due = lambda who: [k["id"] for k in g["who"][who].get("/api/checkins/due").get_json()["due"]]
    assert due("georgina") == [g["P1a"]] and due("peter") == [] and g["P1b"] not in due("georgina")
    g["who"]["georgina"].patch(f"/api/key-results/{g['P1a']}", json={"current": 2})
    assert due("georgina") == []

def test_goals_page(g, app):
    post(g["who"]["trevor"], "/api/objectives", level=3, title="<i>x</i> goal", parent_id=g["T"], owner_id=g["ids"]["Georgina"], **CYC)
    html = g["who"]["georgina"].get("/goals").get_data(as_text=True)
    assert "Fix upload" in html and "Company goal" in html and "Peter private goal" not in html
    assert "&lt;i&gt;x&lt;/i&gt;" in html and "<i>x</i>" not in html
    assert app.test_client().get("/goals").headers["Location"].endswith("/login")
