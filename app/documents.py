import hashlib, io, json, os, re, uuid, zipfile
from flask import Blueprint, jsonify, request, g, send_file
from .auth import login_required, limiter
from .db import db_cursor
from .deliverables import load as load_deliverable, is_manager, err, ser
from .scope import can_view_objective, can_edit_objective, can_view_team_files
from .storage import get_storage

bp = Blueprint("documents", __name__)
DEFAULT_TYPES, DEFAULT_MAX_MB = ["pdf", "docx", "xlsx", "pptx", "csv", "png", "jpg"], 10

def _zip_has(part):
    def check(d):
        try:
            with zipfile.ZipFile(io.BytesIO(d)) as z: names = set(z.namelist())
        except zipfile.BadZipFile: return False
        return "[Content_Types].xml" in names and part in names
    return check

def _text(d):
    try: d.decode("utf-8"); return b"\x00" not in d
    except UnicodeDecodeError: return False

OOXML = "application/vnd.openxmlformats-officedocument."
# ext: (the type we store and serve, content types a browser may legitimately declare, check on the bytes)
TYPES = {
 "pdf": ("application/pdf", {"application/pdf"}, lambda d: d.startswith(b"%PDF-")),
 "png": ("image/png", {"image/png"}, lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
 "jpg": ("image/jpeg", {"image/jpeg"}, lambda d: d.startswith(b"\xff\xd8\xff")),
 "docx": (OOXML + "wordprocessingml.document", {OOXML + "wordprocessingml.document"}, _zip_has("word/document.xml")),
 "xlsx": (OOXML + "spreadsheetml.sheet", {OOXML + "spreadsheetml.sheet"}, _zip_has("xl/workbook.xml")),
 "pptx": (OOXML + "presentationml.presentation", {OOXML + "presentationml.presentation"}, _zip_has("ppt/presentation.xml")),
 "csv": ("text/csv", {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}, _text),
}

def org_setting(cur, org, key, default):
    cur.execute("SELECT value FROM organisation_settings WHERE org_id=%s AND key=%s", (org, key))
    r = cur.fetchone()
    return r["value"] if r else default

def clean_name(n):
    n = os.path.basename((n or "").replace("\\", "/"))
    return re.sub(r"[\x00-\x1f\x7f]", "", n).strip()[:150] or "file"

def parent_access(cur, me, org, ptype, pid):
    """(can_view, can_upload) for the parent, or None if it does not exist or is not visible to the caller."""
    if ptype == "deliverable":
        d = load_deliverable(cur, pid)
        return None if not d else (True, d["owner_id"] == me or is_manager(cur, org, me, d["team_id"]))
    if ptype == "objective":
        cur.execute("SELECT * FROM objectives WHERE id=%s AND org_id=%s", (pid, org)); o = cur.fetchone()
        if not o or not can_view_objective(cur, me, org, o): return None
        return True, can_edit_objective(cur, me, org, o)
    if ptype == "team":
        if not can_view_team_files(cur, me, org, pid): return None
        return True, is_manager(cur, org, me, pid)
    return None

def audit(cur, org, me, action, doc, version):
    cur.execute("INSERT INTO audit_log (org_id, actor_id, action, detail) VALUES (%s,%s,%s,%s)",
                (org, me, action, json.dumps({"document_id": doc, "version": version})))

@bp.post("/api/documents")
@login_required
@limiter.limit("60 per minute")
def upload():
    me, org = g.user["id"], g.user["org_id"]
    ptype, pid = request.form.get("parent_type"), request.form.get("parent_id", type=int)
    f = request.files.get("file")
    if not f or not pid: return err("parent_type, parent_id and a file are required", 422)
    with db_cursor() as cur:
        acc = parent_access(cur, me, org, ptype, pid)
        if not acc: return err("not found", 404)
        if not acc[1]: return err("you cannot add files here", 403)
        allowed = [e.lower() for e in org_setting(cur, org, "file_types", DEFAULT_TYPES)]
        limit = int(float(org_setting(cur, org, "max_file_mb", DEFAULT_MAX_MB)) * 1024 * 1024)
        name = clean_name(f.filename)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        ext = "jpg" if ext == "jpeg" else ext
        if ext not in allowed or ext not in TYPES: return err(f"file type not allowed; allowed: {', '.join(allowed)}", 415)
        data = f.stream.read(limit + 1)
        if len(data) > limit: return err(f"file is larger than {limit // (1024 * 1024) or limit / (1024 * 1024):g} MB", 413)
        if not data: return err("file is empty", 422)
        mime, declared, sniff = TYPES[ext]
        if (f.mimetype or "application/octet-stream") not in declared | {"application/octet-stream"}:
            return err("declared content type does not match the file extension", 415)
        if not sniff(data): return err("file contents do not match its extension", 415)
        doc_id = request.form.get("document_id", type=int)
        if doc_id:
            cur.execute("SELECT id FROM documents WHERE id=%s AND org_id=%s AND parent_type=%s AND parent_id=%s", (doc_id, org, ptype, pid))
        else:                                                    # same name under the same parent = new version
            cur.execute("SELECT id FROM documents WHERE org_id=%s AND parent_type=%s AND parent_id=%s AND lower(name)=lower(%s)", (org, ptype, pid, name))
        row = cur.fetchone()
        if doc_id and not row: return err("document_id does not belong to this parent", 422)
        if row: doc_id = row["id"]
        else:
            cur.execute("INSERT INTO documents (org_id, parent_type, parent_id, name, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                        (org, ptype, pid, name, me))
            doc_id = cur.fetchone()["id"]
        cur.execute("SELECT COALESCE(max(version), 0) + 1 AS v FROM document_versions WHERE document_id=%s", (doc_id,))
        version, key = cur.fetchone()["v"], uuid.uuid4().hex
        get_storage().save(key, data)
        cur.execute("""INSERT INTO document_versions (org_id, document_id, version, storage_key, original_name, content_type,
                       size_bytes, sha256, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (org, doc_id, version, key, name, mime, len(data), hashlib.sha256(data).hexdigest(), me))
        audit(cur, org, me, "upload_document", doc_id, version)
    return jsonify(document_id=doc_id, version=version, name=name), 201

@bp.get("/api/documents")
@login_required
def list_docs():
    me, org = g.user["id"], g.user["org_id"]
    ptype, pid = request.args.get("parent_type"), request.args.get("parent_id", type=int)
    with db_cursor(commit=False) as cur:
        if not pid or not parent_access(cur, me, org, ptype, pid): return err("not found", 404)
        cur.execute("SELECT id, name FROM documents WHERE org_id=%s AND parent_type=%s AND parent_id=%s ORDER BY id", (org, ptype, pid))
        docs = cur.fetchall()
        for d in docs:
            cur.execute("SELECT version, original_name, content_type, size_bytes, uploaded_by, uploaded_at FROM document_versions "
                        "WHERE document_id=%s ORDER BY version", (d["id"],))
            d["versions"] = [ser(v) for v in cur.fetchall()]
    return jsonify(documents=docs)

@bp.get("/api/documents/<int:doc_id>/download")
@login_required
def download(doc_id):
    """The only way to get file bytes. Scope is re-checked on every request, so a copied link is useless to anyone else."""
    me, org = g.user["id"], g.user["org_id"]
    with db_cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE id=%s AND org_id=%s", (doc_id, org)); d = cur.fetchone()
        if not d or not parent_access(cur, me, org, d["parent_type"], d["parent_id"]): return err("not found", 404)
        v = request.args.get("version", type=int)
        cur.execute("SELECT * FROM document_versions WHERE document_id=%s AND (%s IS NULL OR version=%s) ORDER BY version DESC LIMIT 1",
                    (doc_id, v, v))
        ver = cur.fetchone()
        if not ver: return err("not found", 404)
        audit(cur, org, me, "download_document", doc_id, ver["version"])
    resp = send_file(get_storage().open(ver["storage_key"]), mimetype=ver["content_type"], as_attachment=True,
                     download_name=ver["original_name"])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
