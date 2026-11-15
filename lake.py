import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def connect(path):
    return sqlite3.connect(str(path), isolation_level=None, timeout=5)


def encode(rows):
    return b"".join((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class Lake:
    def __init__(self, root):
        self.root = Path(root)
        self.data = self.root / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        with self.session() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS head(version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots(version INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS manifest(
                    version INTEGER, part TEXT, object TEXT, digest TEXT,
                    PRIMARY KEY(version, part));
                INSERT INTO head SELECT 0 WHERE NOT EXISTS(SELECT 1 FROM head);
                INSERT OR IGNORE INTO snapshots VALUES(0);
            """)

    @contextmanager
    def session(self):
        c = connect(self.root / "catalog.db")
        try:
            yield c
        finally:
            if c.in_transaction:
                c.rollback()
            c.close()

    @staticmethod
    def entries(c, version):
        if not c.execute("SELECT 1 FROM snapshots WHERE version=?", (version,)).fetchone():
            raise ValueError("unknown version")
        return [dict(zip(("part", "object", "digest"), row)) for row in c.execute(
            "SELECT part, object, digest FROM manifest WHERE version=? ORDER BY part", (version,))]

    def version(self):
        with self.session() as c:
            return c.execute("SELECT version FROM head").fetchone()[0]

    def commit(self, replacements):
        for part, rows in replacements.items():
            if not isinstance(part, str) or not part.isidentifier():
                raise ValueError("bad partition")
            if not isinstance(rows, list):
                raise ValueError("bad rows")
            for row in rows:
                if (not isinstance(row, list) or len(row) != 2
                    or not isinstance(row[0], str) or type(row[1]) is not int):
                    raise ValueError("bad row")
        with self.session() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute("SELECT version FROM head").fetchone()[0]
            entries = {e["part"]: e for e in self.entries(c, previous)}
            for part, rows in replacements.items():
                if not rows:
                    entries.pop(part, None)
                    continue
                payload = encode(rows)
                sha = digest(payload)
                name = sha + ".jsonl"
                path = self.data / name
                if not path.exists():
                    path.write_bytes(payload)
                entries[part] = {"part": part, "object": name, "digest": sha}
            version = previous + 1
            c.execute("INSERT INTO snapshots VALUES(?)", (version,))
            c.executemany("INSERT INTO manifest VALUES(?,?,?,?)", [
                (version, e["part"], e["object"], e["digest"]) for e in entries.values()])
            c.execute("UPDATE head SET version=?", (version,))
            c.commit()
            return version

    def read(self, version=None):
        with self.session() as c:
            c.execute("BEGIN IMMEDIATE")
            if version is None:
                version = c.execute("SELECT version FROM head").fetchone()[0]
            rows = []
            for entry in self.entries(c, version):
                payload = (self.data / entry["object"]).read_bytes()
                if digest(payload) != entry["digest"]:
                    raise ValueError("digest mismatch")
                rows.extend([entry["part"], *json.loads(line)] for line in payload.splitlines())
            c.commit()
            return sorted(rows)


def clone_catalog(source, destination):
    src = connect(source)
    dst = connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
