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
    raise RuntimeError(f"unknown STORAGE_BACKEND {backend!r}")
