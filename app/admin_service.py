"""Everything that configures an organisation lives here, and nothing in it names a particular organisation.
The admin routes and the seed script both call these functions, so seeding uses the same path as the screens."""
import json
from zoneinfo import ZoneInfo
from .security import hash_password

PERMISSIONS = ["view_all_scorecards", "manage_users", "manage_teams", "manage_settings",
               "approve_any_deliverable", "set_company_goals"]
DEFAULT_ROLES = {"ceo": list(PERMISSIONS),
                 "admin": ["manage_users", "manage_teams", "manage_settings"],
                 "lead": [], "member": []}
FILE_EXTS = ["pdf", "docx", "xlsx", "pptx", "csv", "png", "jpg"]
DEFAULTS = {"timezone": "Africa/Nairobi", "point_scale": [1, 2, 3, 5, 8], "point_labels": {}, "point_rubric": {},
            "file_types": FILE_EXTS, "max_file_mb": 10, "reopen_rule": "restart", "peer_visibility": False,
            "team_leaderboards": False, "comparison_views": False, "on_time_band": [0.8, 0.9], "week_start": "monday",
            "notifications_enabled": True, "notify_due_soon_days": 2}

def _num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)

def _bool(v):
    if not isinstance(v, bool): raise ValueError("must be true or false")
    return v

def _tz(v):
    try: ZoneInfo(v)
    except Exception: raise ValueError("unknown timezone")
    return v

def _scale(v):
    if not (isinstance(v, list) and 1 <= len(v) <= 8 and all(isinstance(x, int) and not isinstance(x, bool) and 0 < x <= 100 for x in v)
            and v == sorted(set(v))):
        raise ValueError("must be 1 to 8 whole numbers in increasing order")
    return v

def _strmap(maxlen):
    def f(v):
        if not (isinstance(v, dict) and len(v) <= 20 and all(isinstance(k, str) and k.isdigit() and isinstance(x, str) and len(x) <= maxlen for k, x in v.items())):
            raise ValueError(f"must map point values to text of at most {maxlen} characters")
        return v
    return f

def _types(v):
    v = ["jpg" if str(x).lower() == "jpeg" else str(x).lower() for x in v] if isinstance(v, list) else []
    if not v or any(x not in FILE_EXTS for x in v): raise ValueError(f"must be a non-empty list from {FILE_EXTS}")
    return sorted(set(v))

def _mb(v):
    if not (_num(v) and 0 < v <= 25): raise ValueError("must be more than 0 and at most 25")
    return v

def _rule(v):
    if v not in ("restart", "first_start"): raise ValueError("must be 'restart' or 'first_start'")
    return v

def _band(v):
    if not (isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v) and 0 <= v[0] < v[1] <= 1):
        raise ValueError("must be [low, high] between 0 and 1")
    return v

def _week(v):
    if v != "monday": raise ValueError("only Monday-to-Sunday weeks are supported")
    return v

def _days(v):
    if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 14): raise ValueError("must be a whole number from 0 (off) to 14")
    return v

VALIDATORS = {"timezone": _tz, "point_scale": _scale, "point_labels": _strmap(30), "point_rubric": _strmap(200),
              "file_types": _types, "max_file_mb": _mb, "reopen_rule": _rule, "peer_visibility": _bool,
              "team_leaderboards": _bool, "comparison_views": _bool, "on_time_band": _band, "week_start": _week,
              "notifications_enabled": _bool, "notify_due_soon_days": _days}

def validate_settings(values):
    if not isinstance(values, dict) or not values: raise ValueError("send an object of settings")
    out = {}
    for k, v in values.items():
        if k not in VALIDATORS: raise ValueError(f"unknown setting '{k}'")
        try: out[k] = VALIDATORS[k](v)
        except ValueError as e: raise ValueError(f"{k}: {e}")
    return out

def effective_settings(cur, org):
    cur.execute("SELECT key, value FROM organisation_settings WHERE org_id=%s", (org,))
    return {**DEFAULTS, **{r["key"]: r["value"] for r in cur.fetchall() if r["key"] in DEFAULTS}}

def save_settings(cur, org, values, actor=None):
    clean, before = validate_settings(values), effective_settings(cur, org)
    for k, v in clean.items():
        cur.execute("INSERT INTO organisation_settings (org_id, key, value) VALUES (%s,%s,%s) "
                    "ON CONFLICT (org_id, key) DO UPDATE SET value=EXCLUDED.value", (org, k, json.dumps(v)))
    cur.execute("INSERT INTO audit_log (org_id, actor_id, action, detail) VALUES (%s,%s,'settings_change',%s)",
                (org, actor, json.dumps({"changed": {k: {"old": before[k], "new": v} for k, v in clean.items() if before[k] != v}})))
    return clean

def create_org(cur, name, slug):
    cur.execute("INSERT INTO organisations (name, slug) VALUES (%s,%s) RETURNING id", (name, slug))
    org = cur.fetchone()["id"]
    for k in PERMISSIONS:
        cur.execute("INSERT INTO permissions (key) VALUES (%s) ON CONFLICT DO NOTHING", (k,))
    for role, perms in DEFAULT_ROLES.items():
        create_role(cur, org, role, perms)
    return org

def create_role(cur, org, name, perms):
    cur.execute("SELECT key FROM permissions WHERE key = ANY(%s)", (list(perms),))
    if {r["key"] for r in cur.fetchall()} != set(perms): raise ValueError("unknown permission")
    cur.execute("INSERT INTO roles (org_id, name) VALUES (%s,%s) RETURNING id", (org, name))
    rid = cur.fetchone()["id"]
    set_role_permissions(cur, org, rid, perms)
    return rid

def set_role_permissions(cur, org, role_id, perms):
    cur.execute("DELETE FROM role_permissions WHERE role_id=%s AND org_id=%s", (role_id, org))
    cur.execute("INSERT INTO role_permissions (org_id, role_id, permission_id) SELECT %s,%s,id FROM permissions WHERE key = ANY(%s)",
                (org, role_id, list(perms)))

def create_team(cur, org, name):
    cur.execute("INSERT INTO teams (org_id, name) VALUES (%s,%s) RETURNING id", (org, name))
    return cur.fetchone()["id"]

def create_user(cur, org, full_name, email=None, password=None):
    cur.execute("INSERT INTO users (org_id, full_name, email, password_hash) VALUES (%s,%s,%s,%s) RETURNING id",
                (org, full_name, email.strip().lower() if email else None, hash_password(password) if password else None))
    return cur.fetchone()["id"]

def add_member(cur, org, team_id, user_id, is_lead=False, reports_to=None):
    cur.execute("""INSERT INTO team_memberships (org_id, team_id, user_id, is_lead, reports_to) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (team_id, user_id) DO UPDATE SET is_lead=EXCLUDED.is_lead, reports_to=EXCLUDED.reports_to""",
                (org, team_id, user_id, is_lead, reports_to))

def set_roles(cur, org, user_id, names):
    cur.execute("SELECT id, name FROM roles WHERE org_id=%s AND name = ANY(%s)", (org, list(names)))
    rows = cur.fetchall()
    if {r["name"] for r in rows} != set(names): raise ValueError("unknown role")
    cur.execute("DELETE FROM user_roles WHERE user_id=%s AND org_id=%s", (user_id, org))
    for r in rows:
        cur.execute("INSERT INTO user_roles (org_id, user_id, role_id) VALUES (%s,%s,%s)", (org, user_id, r["id"]))
