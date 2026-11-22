from datetime import date
from functools import wraps
from flask import Blueprint, jsonify, request, g, render_template, redirect
from flask_jwt_extended import verify_jwt_in_request
from .auth import login_required
from .db import db_cursor
from .metrics import scorecard_for
from .scope import can_view_person
from .admin_service import effective_settings
from .overview import _nav

bp = Blueprint("dashboard", __name__)

def _params():
    try: as_of = date.fromisoformat(request.args["as_of"]) if "as_of" in request.args else None
    except ValueError: as_of = None
    weeks = request.args.get("weeks", 12, type=int)
    return as_of, max(4, min(weeks, 52))

def _fetch(person_id):
    me, org = g.user["id"], g.user["org_id"]
    as_of, weeks = _params()
    with db_cursor() as cur:
        if not can_view_person(cur, me, org, person_id): return None
        if person_id != me:        # views of other people's scorecards are logged
            cur.execute("INSERT INTO audit_log (org_id, actor_id, action, target_user_id) VALUES (%s,%s,'view_scorecard',%s)",
                        (org, me, person_id))
        cur.execute("SELECT full_name FROM users WHERE id=%s", (person_id,))
        name = cur.fetchone()["full_name"]
        data = scorecard_for(cur, org, person_id, as_of, weeks)
        data["band"] = effective_settings(cur, org)["on_time_band"]
        cur.execute("SELECT id, title, points, deadline, status FROM v_deliverable_facts WHERE org_id=%s AND owner_id=%s ORDER BY deadline DESC, id",
                    (org, person_id))
        items = cur.fetchall()
    return name, data, items

@bp.get("/api/people/<int:person_id>/scorecard")
@login_required
def scorecard(person_id):
    r = _fetch(person_id)
    return (jsonify(r[1]), 200) if r else (jsonify(error="not found"), 404)

def page_login(fn):
    @wraps(fn)
    def w(*a, **k):
        try: verify_jwt_in_request()
        except Exception: return redirect("/login")
        return login_required(fn)(*a, **k)
    return w

@bp.get("/login")
def login_page(): return render_template("login.html")

@bp.get("/my-work")
@page_login
def my_work(): return redirect(f"/people/{g.user['id']}")

@bp.get("/people/<int:person_id>")
@page_login
def person_page(person_id):
    r = _fetch(person_id)
    if not r: return render_template("notfound.html"), 404
    name, data, items = r
    with db_cursor(commit=False) as cur:
        nav = _nav(cur, g.user["id"], g.user["org_id"])
    return render_template("dashboard.html", name=name, data=data, items=items, own=person_id == g.user["id"], nav=nav)
