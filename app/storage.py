"""All file bytes go through this one interface. To use S3-compatible storage or Cloudinary, add a class
with the same three methods and select it with STORAGE_BACKEND. Nothing else in the app touches the disk."""
import os, re, tempfile

_KEY = re.compile(r"^[0-9a-f]{32}$")

class Storage:
    def save(self, key, data): raise NotImplementedError
    def open(self, key): raise NotImplementedError
    def delete(self, key): raise NotImplementedError

class LocalStorage(Storage):
    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key):
        if not _KEY.match(key): raise ValueError("bad storage key")        # blocks path traversal
        return os.path.join(self.root, key[:2], key)

    def save(self, key, data):
        path = self._path(key); os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as f: f.write(data)
        os.replace(tmp, path)

    def open(self, key): return open(self._path(key), "rb")
    def delete(self, key):
        try: os.remove(self._path(key))
        except FileNotFoundError: pass

def get_storage():
    backend = os.environ.get("STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalStorage(os.environ.get("STORAGE_DIR", "instance/uploads"))
    if backend == "db":
        return PostgresStorage(os.environ["DATABASE_URL"])
    raise RuntimeError(f"unknown STORAGE_BACKEND {backend!r}")


class PostgresStorage(Storage):
    """Keeps file bytes in Postgres so uploads survive deploys on hosts with no persistent disk."""
    def __init__(self, dsn):
        self.dsn = dsn
        self._ready = False

    def _connect(self):
        try:
            import psycopg
            return psycopg.connect(self.dsn)
        except ImportError:
            import psycopg2
            return psycopg2.connect(self.dsn)

    def _run(self, sql, params=(), fetch=False):
        if not _KEY.match(params[0] if params else "0" * 32): raise ValueError("bad storage key")
        conn = self._connect()
        try:
            cur = conn.cursor()
            if not self._ready:
                cur.execute("CREATE TABLE IF NOT EXISTS file_blobs (storage_key TEXT PRIMARY KEY, data BYTEA NOT NULL)")
                self._ready = True
            cur.execute(sql, params)
            row = cur.fetchone() if fetch else None
            conn.commit()
            return row
        finally:
            conn.close()

    def save(self, key, data):
        self._run("INSERT INTO file_blobs (storage_key, data) VALUES (%s, %s) "
                  "ON CONFLICT (storage_key) DO UPDATE SET data = EXCLUDED.data", (key, bytes(data)))

    def open(self, key):
        import io
        row = self._run("SELECT data FROM file_blobs WHERE storage_key = %s", (key,), fetch=True)
        if row is None: raise FileNotFoundError(key)
        return io.BytesIO(bytes(row[0]))

    def delete(self, key):
        self._run("DELETE FROM file_blobs WHERE storage_key = %s", (key,))
