from flask import Blueprint, jsonify, g
from .auth import login_required
from .db import db_cursor
from .scope import can_view_person, visible_people

bp = Blueprint("people", __name__)

@bp.get("/api/people")
@login_required
def list_people():
    with db_cursor(commit=False) as cur:
        people = visible_people(cur, g.user["id"], g.user["org_id"])
    return jsonify(people=people)   # alphabetical, no ranking

@bp.get("/api/people/<int:person_id>")
@login_required
def person(person_id):
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor() as cur:
        if not can_view_person(cur, me, org, person_id):
            return jsonify(error="not found"), 404     # never reveal that the record exists
        cur.execute("SELECT id, full_name FROM users WHERE id=%s AND org_id=%s", (person_id, org))
        row = cur.fetchone()
        if person_id != me:
            cur.execute("INSERT INTO audit_log (org_id, actor_id, action, target_user_id) "
                        "VALUES (%s,%s,'view_scorecard',%s)", (org, me, person_id))
    return jsonify(row)
