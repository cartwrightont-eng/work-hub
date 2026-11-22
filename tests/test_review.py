"""Review queue: work waiting for approval, shown only to people who may approve it."""
import pytest
from app.db import db_cursor
from tests.test_deliverables import mk, act

def age(did, days):                      # pretend the work has been in review for a while
    with db_cursor() as cur:
        cur.execute("UPDATE status_events SET created_at = now() - make_interval(days => %s) WHERE deliverable_id=%s AND to_status='in_review'", (days, did))

def ids_in(c, key="queue"):
    return [i["id"] for i in c.get("/api/review-queue").get_json()[key]]

@pytest.fixture
def work(login, ids):
    trevor, g_, peter = login("trevor@x.test"), login("georgina@x.test"), login("peter@x.test")
    a = mk(trevor, ids, title="Georgina item").get_json()["id"]
    b = mk(trevor, ids, owner="Peter", title="<i>x</i> Peter item").get_json()["id"]
    c = mk(trevor, ids, owner="Trevor", title="Trevor own", deadline="2020-01-01").get_json()["id"]
    act(g_, a, "start"); act(peter, b, "start"); act(trevor, c, "start")
    assert trevor.patch(f"/api/deliverables/{b}", json={"deadline": "2026-12-15", "reason": "waiting on assets"}).status_code == 200
    g_.post(f"/api/deliverables/{a}/worklog", json={"body": "Ready for review"})
    for c_, d_ in ((g_, a), (peter, b), (trevor, c)): assert act(c_, d_, "submit").status_code == 200
    age(a, 3); age(b, 1)
    return {"a": a, "b": b, "c": c, "trevor": trevor, "georgina": g_, "peter": peter}

def test_queue_shows_only_what_the_viewer_can_approve_oldest_first(work, login):
    a, b, c = work["a"], work["b"], work["c"]
    q = work["trevor"].get("/api/review-queue").get_json()
    mine = [i for i in q["queue"] if i["id"] in (a, b, c)]
    assert [i["id"] for i in mine] == [a, b]                                   # his own work (c) is not his to approve; oldest first
    ga, pb = mine
    assert ga["waiting_days"] == pytest.approx(3.0, abs=0.1) and ga["latest_log"] == "Ready for review" and not ga["resubmitted"]
    assert pb["waiting_days"] == pytest.approx(1.0, abs=0.1) and pb["deadline_moved"] == 1 and pb["owner"]["name"] == "Peter"
    waiting = [i for i in q["waiting_on_others"] if i["id"] == c]
    assert waiting[0]["waiting_on"] == "the CEO" and waiting[0]["needs_ceo"] and waiting[0]["overdue"]
    ceo = [i for i in login("ceo@homerr.test").get("/api/review-queue").get_json()["queue"] if i["id"] in (a, b, c)]
    assert sorted(i["id"] for i in ceo) == sorted([a, b, c]) and next(i for i in ceo if i["id"] == c)["needs_ceo"]
    for who in ("laura@x.test", "ceo@other.test"):                              # other team's lead, other organisation
        assert not set(ids_in(login(who))) & {a, b, c}, who
    assert ids_in(work["peter"]) == []     # members approve nothing
    g_wait = work["georgina"].get("/api/review-queue").get_json()
    assert [i["waiting_on"] for i in g_wait["waiting_on_others"] if i["id"] == a] == ["Trevor"]
    assert b not in [i["id"] for i in g_wait["waiting_on_others"]]              # nobody sees a peer's waiting work

def test_decisions_through_the_queue_update_it(work, login):
    a, b = work["a"], work["b"]
    t = work["trevor"]
    assert act(t, a, "approve").status_code == 200
    assert act(t, b, "reject").status_code == 422                               # reason needed
    assert act(t, b, "reject", "Add the assets").status_code == 200
    assert a not in ids_in(t) and b not in ids_in(t)
    assert act(work["peter"], b, "resume").status_code == 200 and act(work["peter"], b, "submit").status_code == 200
    again = next(i for i in t.get("/api/review-queue").get_json()["queue"] if i["id"] == b)
    assert again["resubmitted"] is True                                         # flagged as a second look
    assert act(login("ceo@homerr.test"), work["c"], "approve").status_code == 200
    assert work["c"] not in ids_in(login("ceo@homerr.test"))

def test_review_page(work, login, app):
    html = work["trevor"].get("/review").get_data(as_text=True)
    assert "Georgina item" in html and "&lt;i&gt;x&lt;/i&gt; Peter item" in html and "<i>x</i>" not in html
    assert ">Approve<" in html and ">Send back<" in html and 'href="/review"' in html
    assert "Trevor own" in html                                                  # in his 'waiting for review' list
    laura = login("laura@x.test").get("/review").get_data(as_text=True)
    assert "Georgina item" not in laura and "Nothing is waiting for your approval" in laura
    g_html = work["georgina"].get("/review").get_data(as_text=True)
    assert "Georgina item" in g_html and 'href="/review"' not in g_html         # members see their own waiting work, no review link
    assert app.test_client().get("/review").headers["Location"].endswith("/login")
