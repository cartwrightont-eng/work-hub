"""The single place that decides who may see whom. Every route calls this.
Reads live database state each time, so role or team changes apply next request."""

def has_permission(cur, user_id, org_id, key):
    cur.execute("""SELECT 1 FROM user_roles ur
        JOIN role_permissions rp ON rp.role_id = ur.role_id AND rp.org_id = ur.org_id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE ur.user_id=%s AND ur.org_id=%s AND p.key=%s LIMIT 1""",
        (user_id, org_id, key))
    return cur.fetchone() is not None

def can_view_person(cur, viewer_id, org_id, target_id):
    cur.execute("SELECT 1 FROM users WHERE id=%s AND org_id=%s AND active", (target_id, org_id))
    if not cur.fetchone():
        return False                      # other org, missing or inactive
    if viewer_id == target_id:
        return True
    if has_permission(cur, viewer_id, org_id, "view_all_scorecards"):
        return True                       # CEO
    cur.execute("SELECT value FROM organisation_settings WHERE org_id=%s AND key='peer_visibility'", (org_id,))
    flag = cur.fetchone()
    if flag and flag["value"] is True:    # organisation switch: team members see each other, never the lead's own page
        cur.execute("""SELECT 1 FROM team_memberships t JOIN team_memberships v ON v.team_id=t.team_id AND v.org_id=t.org_id
            WHERE t.user_id=%s AND t.org_id=%s AND NOT t.is_lead AND v.user_id=%s LIMIT 1""", (target_id, org_id, viewer_id))
        if cur.fetchone():
            return True
    # A lead sees non-lead members of the team they lead (a lead's own page stays private)
    cur.execute("""SELECT 1 FROM team_memberships t
        JOIN team_memberships v ON v.team_id=t.team_id AND v.org_id=t.org_id
        WHERE t.user_id=%s AND t.org_id=%s AND NOT t.is_lead
          AND v.user_id=%s AND v.is_lead LIMIT 1""", (target_id, org_id, viewer_id))
    if cur.fetchone():
        return True
    # Anyone up the target's reporting line sees them; never the reverse
    cur.execute("""WITH RECURSIVE chain(uid) AS (
          SELECT reports_to FROM team_memberships
            WHERE user_id=%s AND org_id=%s AND reports_to IS NOT NULL
          UNION
          SELECT tm.reports_to FROM team_memberships tm JOIN chain c ON tm.user_id=c.uid
            WHERE tm.org_id=%s AND tm.reports_to IS NOT NULL)
        SELECT 1 FROM chain WHERE uid=%s LIMIT 1""", (target_id, org_id, org_id, viewer_id))
    return cur.fetchone() is not None

def visible_people(cur, viewer_id, org_id):
    cur.execute("SELECT id, full_name FROM users WHERE org_id=%s AND active ORDER BY full_name", (org_id,))
    return [u for u in cur.fetchall() if can_view_person(cur, viewer_id, org_id, u["id"])]

def can_view_team(cur, viewer_id, org_id, team_id):
    """Team-level pages: the team's own lead, or anyone holding view_all_scorecards (CEO)."""
    cur.execute("SELECT 1 FROM teams WHERE id=%s AND org_id=%s", (team_id, org_id))
    if not cur.fetchone():
        return False
    if has_permission(cur, viewer_id, org_id, "view_all_scorecards"):
        return True
    cur.execute("SELECT 1 FROM team_memberships WHERE team_id=%s AND org_id=%s AND user_id=%s AND is_lead",
                (team_id, org_id, viewer_id))
    return cur.fetchone() is not None

def can_view_objective(cur, viewer_id, org_id, o):
    """Company goals: everyone in the org. Team goals: that team's members and the CEO. Personal: same rule as scorecards."""
    if o["org_id"] != org_id:
        return False
    if o["level"] == 1:
        return True
    if o["level"] == 2:
        if has_permission(cur, viewer_id, org_id, "view_all_scorecards"):
            return True
        cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s",
                    (org_id, o["team_id"], viewer_id))
        return cur.fetchone() is not None
    return can_view_person(cur, viewer_id, org_id, o["owner_id"])

def can_edit_objective(cur, viewer_id, org_id, o):
    """CEO edits anything; a lead edits their team's and their people's goals; members edit only their own personal goals."""
    if has_permission(cur, viewer_id, org_id, "set_company_goals"):
        return True
    if o["level"] == 1:
        return False
    cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s AND is_lead",
                (org_id, o["team_id"], viewer_id))
    if cur.fetchone():
        return True
    return o["level"] == 3 and o["owner_id"] == viewer_id

def can_view_team_files(cur, viewer_id, org_id, team_id):
    """Team files: everyone on the team, its lead, and the CEO."""
    cur.execute("SELECT 1 FROM teams WHERE id=%s AND org_id=%s", (team_id, org_id))
    if not cur.fetchone():
        return False
    if has_permission(cur, viewer_id, org_id, "view_all_scorecards"):
        return True
    cur.execute("SELECT 1 FROM team_memberships WHERE org_id=%s AND team_id=%s AND user_id=%s", (org_id, team_id, viewer_id))
    return cur.fetchone() is not None
