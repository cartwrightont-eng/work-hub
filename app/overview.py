"""Team and organisation dashboards. Rosters are alphabetical and unranked; each row shows a person
against their own previous period. Points never appear in a cross-person table."""
from datetime import date
from functools import wraps
from flask import Blueprint, jsonify, request, g, render_template, redirect
from flask_jwt_extended import verify_jwt_in_request
from .auth import login_required
from .db import db_cursor
from .metrics import build, compare, org_today
from .scope import can_view_person, can_view_team, has_permission

bp = Blueprint("overview", __name__)
PERIODS = (4, 8, 12, 26)

def _period(cur, org):
    try: as_of = date.fromisoformat(request.args["as_of"]) if "as_of" in request.args else org_today(cur, org)
    except ValueError: as_of = org_today(cur, org)
    weeks = request.args.get("weeks", 4, type=int)
    return as_of, (weeks if weeks in PERIODS else 4)

def _facts_by_owner(cur, org, ids):
    cur.execute("SELECT * FROM v_deliverable_facts WHERE org_id=%s AND owner_id = ANY(%s) ORDER BY id", (org, list(ids)))
    out = {i: [] for i in ids}
    for f in cur.fetchall(): out[f["owner_id"]].append(f)
    return out

def _log_views(cur, org, me, person_ids, via):
    for pid in person_ids:
        if pid != me:
            cur.execute("INSERT INTO audit_log (org_id, actor_id, action, target_user_id, detail) VALUES (%s,%s,'view_scorecard',%s,%s)",
                        (org, me, pid, '{"via": "%s"}' % via))

def team_data(cur, me, org, team_id, as_of, weeks):
    cur.execute("SELECT id, name FROM teams WHERE id=%s", (team_id,)); team = cur.fetchone()
    cur.execute("""SELECT u.id, u.full_name, m.is_lead FROM team_memberships m JOIN users u ON u.id=m.user_id
                   WHERE m.team_id=%s AND m.org_id=%s AND u.active ORDER BY u.full_name""", (team_id, org))
    members = [m for m in cur.fetchall() if can_view_person(cur, me, org, m["id"])]
    facts = _facts_by_owner(cur, org, [m["id"] for m in members])
    people, charts = [], []
    for m in members:
        c = compare(facts[m["id"]], as_of, weeks)
        people.append({"id": m["id"], "name": m["full_name"], "role": "Lead" if m["is_lead"] else "Member", **c})
        wr = build(facts[m["id"]], as_of, max(weeks, 8))["work_rate"]     # own trend, own axis
        charts.append({"id": m["id"], "name": m["full_name"],
                       "labels": [f"{w['week']} · n={w['tasks']}" for w in wr], "points": [w["points"] for w in wr]})
    allf = [f for m in members for f in facts[m["id"]]]
    _log_views(cur, org, me, [m["id"] for m in members], "team")
    return {"team": team, "as_of": as_of.isoformat(), "period_weeks": weeks,
            "summary": compare(allf, as_of, weeks), "people": people, "charts": charts}

def org_data(cur, me, org, as_of, weeks):
    cur.execute("""SELECT u.id, u.full_name, t.id AS team_id, t.name AS team, COALESCE(m.is_lead, FALSE) AS is_lead
                   FROM users u LEFT JOIN team_memberships m ON m.user_id=u.id AND m.org_id=u.org_id
                   LEFT JOIN teams t ON t.id=m.team_id WHERE u.org_id=%s AND u.active ORDER BY u.full_name""", (org,))
    users = cur.fetchall()
    facts = _facts_by_owner(cur, org, [u["id"] for u in users])
    teams = []
    cur.execute("SELECT id, name FROM teams WHERE org_id=%s ORDER BY name", (org,))
    for t in cur.fetchall():
        mem = [u for u in users if u["team_id"] == t["id"]]
        lead = next((u["full_name"] for u in mem if u["is_lead"]), None)
        teams.append({"id": t["id"], "name": t["name"], "lead": lead, "people": len(mem),
                      **compare([f for u in mem for f in facts[u["id"]]], as_of, weeks)})
    people = [{"id": u["id"], "name": u["full_name"],
               "role": f"{u['team']} · {'Lead' if u['is_lead'] else 'Member'}" if u["team"] else "CEO / no team",
               **compare(facts[u["id"]], as_of, weeks)} for u in users]
    _log_views(cur, org, me, [u["id"] for u in users], "org")
    return {"as_of": as_of.isoformat(), "period_weeks": weeks, "teams": teams, "people": people}

def _nav(cur, me, org):
    nav = [("My work", "/my-work"), ("Work", "/work"), ("Goals", "/goals")]
    ceo = has_permission(cur, me, org, "view_all_scorecards")
    if ceo:
        nav += [("Organisation", "/org"), ("Settings", "/settings"), ("People", "/admin/people")]
        cur.execute("SELECT id, name FROM teams WHERE org_id=%s ORDER BY name", (org,))
    else:
        cur.execute("SELECT t.id, t.name FROM teams t JOIN team_memberships m ON m.team_id=t.id "
                    "WHERE t.org_id=%s AND m.user_id=%s AND m.is_lead ORDER BY t.name", (org, me))
    teams = cur.fetchall()
    if ceo or teams: nav.append(("Review", "/review"))
    return nav + [(f"Team: {t['name']}", f"/team/{t['id']}") for t in teams]

def _team(team_id):
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor() as cur:
        if not can_view_team(cur, me, org, team_id): return None
        as_of, weeks = _period(cur, org)
        return team_data(cur, me, org, team_id, as_of, weeks), _nav(cur, me, org)

def _org():
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor() as cur:
        if not has_permission(cur, me, org, "view_all_scorecards"): return None
        as_of, weeks = _period(cur, org)
        return org_data(cur, me, org, as_of, weeks), _nav(cur, me, org)

def page_login(fn):
    @wraps(fn)
    def w(*a, **k):
        try: verify_jwt_in_request()
        except Exception: return redirect("/login")
        return login_required(fn)(*a, **k)
    return w

@bp.get("/api/teams/<int:team_id>/overview")
@login_required
def api_team(team_id):
    r = _team(team_id)
    return (jsonify(r[0]), 200) if r else (jsonify(error="not found"), 404)

@bp.get("/api/org/overview")
@login_required
def api_org():
    r = _org()
    return (jsonify(r[0]), 200) if r else (jsonify(error="not found"), 404)

@bp.get("/team/<int:team_id>")
@page_login
def team_page(team_id):
    r = _team(team_id)
    return render_template("team.html", data=r[0], nav=r[1], periods=PERIODS) if r else (render_template("notfound.html"), 404)

@bp.get("/org")
@page_login
def org_page():
    r = _org()
    return render_template("org.html", data=r[0], nav=r[1], periods=PERIODS) if r else (render_template("notfound.html"), 404)
