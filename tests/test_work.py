"""Work screens: the deliverables board, the deliverable page, and the goal forms.
The pages only decide what to *show*; the actions behind the buttons are the existing API and are tested elsewhere.
What is tested here: who sees which cards, which buttons appear for whom, and that user-typed text is escaped."""
import html as htmllib
import json
import re
import uuid
import pytest
from app.db import db_cursor
from tests.test_deliverables import mk, act

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20

def tag(label="x"): return f"{label}-{uuid.uuid4().hex[:8]}"

def card(page, did):
    m = re.search(rf'<article class="dcard" data-id="{did}">.*?</article>', page, re.S)
    return m.group(0) if m else None

def column(page, label):
    m = re.search(rf'<section class="col" aria-label="{re.escape(label)}">.*?</section>', page, re.S)
    return m.group(0) if m else ""

def form_data(page):
    return json.loads(re.search(r'<script id="form-data"[^>]*>(.*?)</script>', page, re.S).group(1))

def tech_id(ids):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT id FROM teams WHERE name='Tech' AND org_id=%s", (ids["org"],))
        return cur.fetchone()["id"]

def test_pages_need_a_login(app):
    c = app.test_client()
    for url in ("/work", "/work/1"):
        r = c.get(url)
        assert r.status_code == 302 and r.headers["Location"].endswith("/login"), url

def test_board_shows_only_work_the_viewer_may_see_in_the_right_columns(login, ids):
    trevor, geo, peter, laura = login("trevor@x.test"), login("georgina@x.test"), login("peter@x.test"), login("laura@x.test")
    t_g, t_p, t_u = tag("Gina"), tag("Pete"), tag("Unsized")
    a = mk(trevor, ids, title=t_g + " <b>bold</b>").get_json()["id"]
    b = mk(trevor, ids, owner="Peter", title=t_p).get_json()["id"]
    u = mk(trevor, ids, title=t_u, points=None).get_json()["id"]
    assert act(geo, a, "start").status_code == 200

    g_page = geo.get("/work").get_data(as_text=True)
    assert t_g in g_page and t_p not in g_page                              # her own work only
    assert "&lt;b&gt;bold&lt;/b&gt;" in g_page and "<b>bold</b>" not in g_page   # typed titles are escaped
    assert f'data-id="{a}"' in column(g_page, "In progress") and f'data-id="{u}"' in column(g_page, "Backlog")
    assert 'data-action="submit"' in card(g_page, a)                         # she can send her own started work
    assert 'id="create"' not in g_page                                       # members cannot assign work

    t_page = trevor.get("/work").get_data(as_text=True)
    assert t_g in t_page and t_p in t_page                                   # a lead sees the team
    assert 'id="create"' in t_page and 'name="size"' in t_page
    assert 'data-action="start"' in card(t_page, b)                          # a lead may start a teammate's sized work
    assert 'data-action="submit"' not in card(t_page, a)                     # but only the owner submits
    assert 'data-action="start"' not in card(t_page, u) and "Needs a size" in card(t_page, u)

    for who in (laura, login("ceo@other.test"), peter):
        page = who.get("/work").get_data(as_text=True)
        assert t_g not in page, who
    assert t_p in peter.get("/work").get_data(as_text=True)                  # Peter does see his own

def test_size_picker_shows_the_organisations_own_scale(login, ids):
    page = login("trevor@x.test").get("/work").get_data(as_text=True)
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT value FROM organisation_settings WHERE org_id=%s AND key='point_scale'", (ids["org"],))
        row = cur.fetchone()
    scale = row["value"] if row else [1, 2, 3, 5, 8]
    values = re.findall(r'name="size" value="(\d*)"', page)
    assert values == [""] + [str(p) for p in scale]

def test_team_filter_limits_the_board(login, ids):
    trevor = login("trevor@x.test")
    title = tag("Filtered")
    mk(trevor, ids, title=title)
    assert title in trevor.get(f"/work?team={tech_id(ids)}").get_data(as_text=True)
    assert title not in trevor.get("/work?team=999999").get_data(as_text=True)

def test_done_column_only_lists_recent_completions(login, ids):
    trevor, geo = login("trevor@x.test"), login("georgina@x.test")
    new, old = tag("DoneNew"), tag("DoneOld")
    ids_ = []
    for t in (new, old):
        d = mk(trevor, ids, title=t).get_json()["id"]
        for c, a in ((geo, "start"), (geo, "submit"), (trevor, "approve")): assert act(c, d, a).status_code == 200
        ids_.append(d)
    with db_cursor() as cur:
        cur.execute("UPDATE status_events SET created_at = now() - interval '45 days' WHERE deliverable_id=%s AND to_status='done'", (ids_[1],))
    done = column(trevor.get("/work").get_data(as_text=True), "Done, last 30 days")
    assert new in done and old not in done

def test_detail_page_buttons_follow_role_and_status(login, ids):
    trevor, geo, peter = login("trevor@x.test"), login("georgina@x.test"), login("peter@x.test")
    d = mk(trevor, ids, title=tag("Detail")).get_json()["id"]
    url = f"/work/{d}"
    owner = geo.get(url).get_data(as_text=True)
    assert 'data-action="start"' in owner and 'id="logform"' in owner and 'id="amend"' not in owner
    lead = trevor.get(url).get_data(as_text=True)
    assert 'id="amend"' in lead and 'id="logform"' not in lead               # the lead changes size and dates; only the owner logs work
    assert peter.get(url).status_code == 404                                  # a peer cannot even tell it exists
    assert login("laura@x.test").get(url).status_code == 404
    assert login("ceo@other.test").get(url).status_code == 404

    assert act(geo, d, "start").status_code == 200 and act(geo, d, "submit").status_code == 200
    lead, owner = trevor.get(url).get_data(as_text=True), geo.get(url).get_data(as_text=True)
    assert 'data-action="approve"' in lead and 'data-action="reject"' in lead
    assert 'data-action="approve"' not in owner and 'data-action="start"' not in owner
    assert act(trevor, d, "reject", "Needs the edge case").status_code == 200
    owner = geo.get(url).get_data(as_text=True)
    assert 'data-action="resume"' in owner and "Needs the edge case" in owner and "Sent back" in owner

def test_unsized_work_cannot_be_started_from_the_page(login, ids):
    trevor, geo = login("trevor@x.test"), login("georgina@x.test")
    d = mk(trevor, ids, title=tag("NoSize"), points=None).get_json()["id"]
    page = geo.get(f"/work/{d}").get_data(as_text=True)
    assert 'data-action="start"' not in page and "Not sized yet" in page

def test_work_log_documents_and_history_render_safely(login, ids):
    trevor, geo, peter = login("trevor@x.test"), login("georgina@x.test"), login("peter@x.test")
    d = mk(trevor, ids, title=tag("Panels")).get_json()["id"]
    assert geo.post(f"/api/deliverables/{d}/worklog", json={"body": "<script>alert(1)</script> done the first part"}).status_code == 201
    r = geo.post("/api/documents", data={"parent_type": "deliverable", "parent_id": str(d), "file": (__import__("io").BytesIO(PNG), "a&b<c>.png", "image/png")})
    assert r.status_code == 201, r.get_json()
    doc = r.get_json()["document_id"]
    assert geo.post("/api/documents", data={"parent_type": "deliverable", "parent_id": str(d), "file": (__import__("io").BytesIO(PNG + b"1"), "a&b<c>.png", "image/png")}).status_code == 201
    assert trevor.patch(f"/api/deliverables/{d}", json={"deadline": "2027-01-15", "reason": "<u>waiting</u> on assets"}).status_code == 200

    for who in (geo, trevor):
        page = who.get(f"/work/{d}").get_data(as_text=True)
        assert "&lt;script&gt;alert(1)&lt;/script&gt; done the first part" in page and "<script>alert(1)</script>" not in page
        assert "a&amp;b&lt;c&gt;.png" in page and "a&b<c>" not in page
        assert f'/api/documents/{doc}/download?version=2"' in page and f'/api/documents/{doc}/download?version=1"' in page
        assert "(latest)" in page and 'id="upload"' in page                   # the owner and the lead may add files
        assert "Originally" in page and "&lt;u&gt;waiting&lt;/u&gt; on assets" in page and "<u>waiting</u>" not in page
    link = f"/api/documents/{doc}/download?version=1"
    assert geo.get(link).status_code == 200 and peter.get(link).status_code == 404

def test_link_to_goal_offers_only_goals_the_viewer_may_use(login, ids):
    ceo, trevor, geo = login("ceo@homerr.test"), login("trevor@x.test"), login("georgina@x.test")
    t = tech_id(ids)
    comp = ceo.post("/api/objectives", json={"level": 1, "title": tag("CompanyGoal")}).get_json()["id"]
    team = trevor.post("/api/objectives", json={"level": 2, "title": tag("TechGoal"), "parent_id": comp, "team_id": t}).get_json()["id"]
    mine = geo.post("/api/objectives", json={"level": 3, "title": tag("GinaGoal"), "parent_id": team}).get_json()["id"]
    peters = login("peter@x.test").post("/api/objectives", json={"level": 3, "title": tag("PeterGoal"), "parent_id": team}).get_json()["id"]
    k_mine = geo.post(f"/api/objectives/{mine}/key-results", json={"title": "gina kr", "target": 5}).get_json()["id"]
    k_peter = login("peter@x.test").post(f"/api/objectives/{peters}/key-results", json={"title": "peter kr", "target": 5}).get_json()["id"]
    d = mk(trevor, ids, title=tag("Linkable")).get_json()["id"]
    page = geo.get(f"/work/{d}").get_data(as_text=True)
    assert f'value="{k_mine}"' in page and f'value="{k_peter}"' not in page   # never a peer's personal key result
    assert geo.post(f"/api/deliverables/{d}/link", json={"key_result_id": k_mine}).status_code == 200
    assert "gina kr" in geo.get(f"/work/{d}").get_data(as_text=True)

def test_goal_form_offers_what_each_person_may_create(login, ids):
    ceo, trevor, geo, laura = login("ceo@homerr.test"), login("trevor@x.test"), login("georgina@x.test"), login("laura@x.test")
    t = tech_id(ids)
    comp = ceo.post("/api/objectives", json={"level": 1, "title": tag("FormCompany")}).get_json()["id"]
    team = trevor.post("/api/objectives", json={"level": 2, "title": tag("FormTech"), "parent_id": comp, "team_id": t}).get_json()["id"]

    c_form = form_data(ceo.get("/goals").get_data(as_text=True))
    assert c_form["company"] and c_form["ceo"] and {x["id"] for x in c_form["teams"]} >= {t}
    t_form = form_data(trevor.get("/goals").get_data(as_text=True))
    assert not t_form["company"] and [x["id"] for x in t_form["teams"]] == [t] and t in t_form["lead_of"]
    names = {m["name"] for m in t_form["members"][str(t)]}
    assert {"Georgina", "Peter", "Trevor"} <= names
    g_form = form_data(geo.get("/goals").get_data(as_text=True))
    assert not g_form["company"] and g_form["teams"] == [] and g_form["lead_of"] == []   # members create only personal goals
    assert any(p["id"] == team for p in g_form["parents"])
    l_form = form_data(laura.get("/goals").get_data(as_text=True))
    assert str(t) not in l_form["members"] and all(p["id"] != team for p in l_form["parents"])   # another team's goal is invisible
    o_form = form_data(login("ceo@other.test").get("/goals").get_data(as_text=True))
    assert all(p["id"] not in (comp, team) for p in o_form["parents"])

    assert f'data-goal="{comp}"' in ceo.get("/goals").get_data(as_text=True)        # the CEO can add key results to it
    assert f'data-goal="{comp}"' not in geo.get("/goals").get_data(as_text=True)    # a member cannot
    assert f'data-goal="{team}"' in trevor.get("/goals").get_data(as_text=True)     # the lead can on a team goal

def test_every_page_has_navigation_with_a_work_link(login, ids):
    geo, trevor = login("georgina@x.test"), login("trevor@x.test")
    for who, url in ((geo, f"/people/{ids['Georgina']}"), (geo, "/work"), (geo, "/goals"), (trevor, "/review")):
        page = who.get(url).get_data(as_text=True)
        assert '<nav aria-label="Main">' in page and 'href="/work"' in page, url
    own = geo.get(f"/people/{ids['Georgina']}").get_data(as_text=True)
    assert 'href="/review"' not in own and 'id="c1"' in own and 'class="chart"' in own
    assert 'href="/review"' in trevor.get("/work").get_data(as_text=True)
    assert 'aria-current="page"' in geo.get("/work").get_data(as_text=True)
