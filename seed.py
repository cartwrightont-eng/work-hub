"""Create an organisation from a JSON file using the same services as the admin screens.
Usage: python seed.py seeds/<organisation>.json
Reads HUB_CEO_EMAIL and HUB_CEO_PASSWORD from the environment, or prompts for them."""
import getpass, json, os, sys
from app.db import db_cursor, init_db
from app import admin_service as svc

def seed_org(cur, spec, ceo_email, ceo_password):
    org = svc.create_org(cur, spec["organisation"]["name"], spec["organisation"]["slug"])
    if spec.get("settings"):
        svc.save_settings(cur, org, spec["settings"])
    ceo = svc.create_user(cur, org, spec["ceo"]["name"], ceo_email, ceo_password)
    svc.set_roles(cur, org, ceo, ["ceo", "admin"])
    ids = {}
    for t in spec["teams"]:
        team = svc.create_team(cur, org, t["name"])
        lead = ids[t["lead"]] = svc.create_user(cur, org, t["lead"])
        svc.add_member(cur, org, team, lead, is_lead=True)
        svc.set_roles(cur, org, lead, ["lead"])
        for m in t["members"]:
            uid = ids[m["name"]] = svc.create_user(cur, org, m["name"])
            svc.add_member(cur, org, team, uid, reports_to=ids.get(m.get("reports_to")))
            svc.set_roles(cur, org, uid, ["member"])
    return org

if __name__ == "__main__":
    spec = json.load(open(sys.argv[1]))
    email = os.environ.get("HUB_CEO_EMAIL") or input("CEO email: ")
    pw = os.environ.get("HUB_CEO_PASSWORD") or getpass.getpass("CEO password: ")
    init_db()
    with db_cursor() as cur:
        print("Created organisation id", seed_org(cur, spec, email, pw))
