import os, tempfile, pytest
os.environ.setdefault("MAIL_BACKEND", "memory")
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="hub-test-"))
os.environ.setdefault("DATABASE_URL", "postgresql:///workhub_test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-production-0123456789")
from app import create_app
from app.db import db_cursor, init_db
from seed import seed_org

PW = "Correct-Horse-42x"
SPEC = {"organisation": {"name": "Homerr", "slug": "homerr"}, "ceo": {"name": "Sebastian Rague"},
        "teams": [{"name": "Marketing", "lead": "Laura", "members": []},
                  {"name": "Tech", "lead": "Trevor", "members": [
                      {"name": "Georgina", "reports_to": "Trevor"},
                      {"name": "Peter", "reports_to": "Trevor"}]}]}
OTHER = {"organisation": {"name": "Other Co", "slug": "other"}, "ceo": {"name": "Olive"},
         "teams": [{"name": "Sales", "lead": "Sam", "members": []}]}

@pytest.fixture(scope="session")
def ids():
    init_db()
    with db_cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    init_db()
    out = {}
    with db_cursor() as cur:
        out["org"] = seed_org(cur, SPEC, "ceo@homerr.test", PW)
        out["other_org"] = seed_org(cur, OTHER, "ceo@other.test", PW)
        from app.security import hash_password
        h = hash_password(PW)
        for name, email in [("Laura", "laura"), ("Trevor", "trevor"), ("Georgina", "georgina"),
                            ("Peter", "peter"), ("Sam", "sam")]:
            cur.execute("UPDATE users SET email=%s, password_hash=%s WHERE full_name=%s",
                        (email + "@x.test", h, name))
        cur.execute("SELECT id, full_name FROM users")
        out.update({r["full_name"]: r["id"] for r in cur.fetchall()})
    return out

@pytest.fixture
def app(ids):
    return create_app(testing=True)

@pytest.fixture
def login(app):
    def _login(email, csrf=True):
        c = app.test_client()
        assert c.post("/auth/login", json={"email": email, "password": PW}).status_code == 200
        if csrf:
            c.environ_base['HTTP_X_CSRF_TOKEN'] = c.get_cookie('csrf_access_token').value
        return c
    return _login
