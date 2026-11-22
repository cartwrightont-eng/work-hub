"""Phase 6 acceptance: an unauthorised user cannot fetch a file even with its direct link."""
import io, os, zipfile
import pytest
from app.db import db_cursor
from tests.test_deliverables import mk

def zipdoc(part):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>"); z.writestr(part, "<x/>")
    return b.getvalue()

O = "application/vnd.openxmlformats-officedocument."
SAMPLES = {  # ext: (bytes, content type a browser would send)
 "pdf": (b"%PDF-1.4\n1 0 obj\n%%EOF", "application/pdf"),
 "png": (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png"),
 "jpg": (b"\xff\xd8\xff\xe0" + b"0" * 20, "image/jpeg"),
 "docx": (zipdoc("word/document.xml"), O + "wordprocessingml.document"),
 "xlsx": (zipdoc("xl/workbook.xml"), O + "spreadsheetml.sheet"),
 "pptx": (zipdoc("ppt/presentation.xml"), O + "presentationml.presentation"),
 "csv": (b"a,b\n1,2\n", "text/csv"),
}
PDF, PDF_CT = SAMPLES["pdf"]

def up(c, ptype, pid, name, data, ctype="application/pdf", **extra):
    return c.post("/api/documents", content_type="multipart/form-data",
                  data={"parent_type": ptype, "parent_id": str(pid), "file": (io.BytesIO(data), name, ctype), **extra})

@pytest.fixture
def w(login):
    return {n: login(e) for n, e in [("ceo", "ceo@homerr.test"), ("trevor", "trevor@x.test"), ("laura", "laura@x.test"),
                                     ("georgina", "georgina@x.test"), ("peter", "peter@x.test"), ("other", "ceo@other.test")]}

@pytest.fixture
def did(w, ids):                                   # Georgina's deliverable
    return mk(w["trevor"], ids).get_json()["id"]

def test_roundtrip_and_storage_never_uses_the_uploaded_name(w, did):
    r = up(w["georgina"], "deliverable", did, "Quarterly Plan.pdf", PDF)
    assert r.status_code == 201 and r.get_json()["version"] == 1
    doc = r.get_json()["document_id"]
    d = w["georgina"].get(f"/api/documents/{doc}/download")
    assert d.status_code == 200 and d.data == PDF
    assert "attachment" in d.headers["Content-Disposition"] and "Quarterly Plan.pdf" in d.headers["Content-Disposition"]
    assert d.headers["X-Content-Type-Options"] == "nosniff" and d.headers["Content-Type"] == "application/pdf"
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT storage_key FROM document_versions WHERE document_id=%s", (doc,))
        key = cur.fetchone()["storage_key"]
    assert len(key) == 32 and "Quarterly" not in key
    on_disk = [f for _, _, fs in os.walk(os.environ["STORAGE_DIR"]) for f in fs]
    assert key in on_disk and not any("Quarterly" in f for f in on_disk)
    listed = w["georgina"].get(f"/api/documents?parent_type=deliverable&parent_id={did}").get_json()["documents"]
    assert listed[0]["name"] == "Quarterly Plan.pdf" and "storage_key" not in str(listed)

def test_new_upload_over_same_name_is_a_new_version_and_old_stays(w, did):
    v1, v2, v3 = PDF, PDF + b"second", PDF + b"third"
    doc = up(w["georgina"], "deliverable", did, "Spec.pdf", v1).get_json()["document_id"]
    r2 = up(w["georgina"], "deliverable", did, "spec.PDF", v2).get_json()           # same name, any case
    assert (r2["document_id"], r2["version"]) == (doc, 2)
    r3 = up(w["trevor"], "deliverable", did, "Spec final.pdf", v3, document_id=str(doc)).get_json()   # forced new version
    assert (r3["document_id"], r3["version"]) == (doc, 3)
    get = lambda q="": w["georgina"].get(f"/api/documents/{doc}/download{q}")
    assert get().data == v3 and get("?version=1").data == v1 and get("?version=2").data == v2
    assert get("?version=9").status_code == 404
    vs = w["georgina"].get(f"/api/documents?parent_type=deliverable&parent_id={did}").get_json()["documents"][0]["versions"]
    assert [v["version"] for v in vs] == [1, 2, 3]
    assert up(w["georgina"], "deliverable", did, "Other.pdf", v1, document_id=str(doc + 9999)).status_code == 422

def test_validation(w, did):
    g = w["georgina"]
    for ext, (data, ct) in SAMPLES.items():                                          # every default type is accepted
        assert up(g, "deliverable", did, f"file.{ext}", data, ct).status_code == 201, ext
    assert up(g, "deliverable", did, "photo.jpeg", SAMPLES["jpg"][0], "image/jpeg").status_code == 201
    assert up(g, "deliverable", did, "run.exe", b"MZ\x90\x00", "application/octet-stream").status_code == 415
    assert up(g, "deliverable", did, "run.pdf.exe", PDF).status_code == 415            # only the last extension counts
    assert up(g, "deliverable", did, "evil.pdf", b"MZ\x90\x00 not a pdf").status_code == 415       # bytes must match
    assert up(g, "deliverable", did, "pic.pdf", SAMPLES["png"][0]).status_code == 415
    assert up(g, "deliverable", did, "fake.docx", b"PK\x03\x04junk", SAMPLES["docx"][1]).status_code == 415
    assert up(g, "deliverable", did, "bin.csv", b"a,b\x00\x01", "text/csv").status_code == 415
    assert up(g, "deliverable", did, "x.pdf", PDF, "text/html").status_code == 415      # declared type must fit
    assert up(g, "deliverable", did, "empty.pdf", b"").status_code == 422
    assert up(g, "deliverable", did, "big.pdf", PDF + b"0" * (10 * 1024 * 1024)).status_code == 413
    assert g.post("/api/documents", data={"parent_type": "deliverable", "parent_id": str(did)}).status_code == 422
    assert up(g, "deliverable", did, "../../etc/passwd.pdf", PDF).get_json()["name"] == "passwd.pdf"   # path stripped

def test_limits_and_types_are_configurable_per_organisation(w, ids):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT id FROM teams WHERE org_id=%s", (ids["other_org"],)); team = cur.fetchone()["id"]
    def setting(k, v):
        with db_cursor() as cur:
            cur.execute("INSERT INTO organisation_settings (org_id,key,value) VALUES (%s,%s,%s) ON CONFLICT (org_id,key) DO UPDATE SET value=EXCLUDED.value",
                        (ids["other_org"], k, v))
    setting("file_types", '["pdf"]'); setting("max_file_mb", "1")
    try:
        assert up(w["other"], "team", team, "a.png", SAMPLES["png"][0], "image/png").status_code == 415
        assert up(w["other"], "team", team, "a.pdf", PDF + b"0" * (1024 * 1024)).status_code == 413
        assert up(w["other"], "team", team, "a.pdf", PDF).status_code == 201
    finally:
        setting("file_types", '["pdf","docx","xlsx","pptx","csv","png","jpg"]'); setting("max_file_mb", "10")

def test_unauthorised_user_cannot_fetch_even_with_the_direct_link(w, did, app):
    doc = up(w["georgina"], "deliverable", did, "Private.pdf", PDF).get_json()["document_id"]
    link = f"/api/documents/{doc}/download"
    for who in ("trevor", "ceo", "georgina"):
        assert w[who].get(link).status_code == 200, who
    for who in ("peter", "laura", "other"):                                       # peer, other team's lead, other organisation
        assert w[who].get(link).status_code == 404, who
        assert w[who].get(link + "?version=1").status_code == 404
        assert w[who].get(f"/api/documents?parent_type=deliverable&parent_id={did}").status_code == 404
        assert up(w[who], "deliverable", did, "Sneaky.pdf", PDF).status_code == 404
    assert app.test_client().get(link).status_code == 401                         # not signed in
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT storage_key FROM document_versions WHERE document_id=%s", (doc,)); key = cur.fetchone()["storage_key"]
    for guess in (f"/uploads/{key}", f"/static/{key}", f"/instance/uploads/{key[:2]}/{key}", f"/files/{key}"):
        assert w["ceo"].get(guess).status_code == 404                              # no public path to the bytes, even for the CEO
    assert up(w["trevor"], "deliverable", did, "Lead note.pdf", PDF).status_code == 201     # lead may add files to her team's work

def test_team_and_goal_files_follow_their_parent(w, ids):
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT id FROM teams WHERE org_id=%s AND name='Tech'", (ids["org"],)); tech = cur.fetchone()["id"]
    doc = up(w["trevor"], "team", tech, "Runbook.pdf", PDF).get_json()["document_id"]
    link = f"/api/documents/{doc}/download"
    assert [w[n].get(link).status_code for n in ("trevor", "georgina", "peter", "ceo")] == [200, 200, 200, 200]
    assert [w[n].get(link).status_code for n in ("laura", "other")] == [404, 404]
    assert up(w["georgina"], "team", tech, "Mine.pdf", PDF).status_code == 403          # members read team files, leads add them
    # goals: company goal files are visible to all; a personal goal's files follow the scorecard rules
    cyc = {"cycle_start": "2026-10-01", "cycle_end": "2026-12-31"}
    mkobj = lambda c, **j: c.post("/api/objectives", json={**cyc, **j}).get_json()["id"]
    C = mkobj(w["ceo"], level=1, title="Docs company goal")
    T = mkobj(w["trevor"], level=2, title="Docs team goal", parent_id=C, team_id=tech)
    P = mkobj(w["georgina"], level=3, title="Docs personal goal", parent_id=T)
    cdoc = up(w["ceo"], "objective", C, "Strategy.pdf", PDF).get_json()["document_id"]
    pdoc = up(w["georgina"], "objective", P, "Evidence.pdf", PDF).get_json()["document_id"]
    assert [w[n].get(f"/api/documents/{cdoc}/download").status_code for n in ("georgina", "laura", "ceo")] == [200, 200, 200]
    assert w["other"].get(f"/api/documents/{cdoc}/download").status_code == 404
    assert [w[n].get(f"/api/documents/{pdoc}/download").status_code for n in ("georgina", "trevor", "ceo")] == [200, 200, 200]
    assert [w[n].get(f"/api/documents/{pdoc}/download").status_code for n in ("peter", "laura", "other")] == [404, 404, 404]
    assert up(w["georgina"], "objective", C, "x.pdf", PDF).status_code == 403          # members cannot add to company goals

def test_uploads_and_downloads_are_audited(w, did):
    with db_cursor() as cur: cur.execute("DELETE FROM audit_log")
    doc = up(w["georgina"], "deliverable", did, "Audit.pdf", PDF).get_json()["document_id"]
    w["trevor"].get(f"/api/documents/{doc}/download"); w["peter"].get(f"/api/documents/{doc}/download")
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT action, actor_id FROM audit_log ORDER BY id"); rows = [(r["action"]) for r in cur.fetchall()]
    assert rows == ["upload_document", "download_document"]                            # the refused download by Peter is not a download
