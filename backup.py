import json
from pathlib import Path

from lake import Lake, clone_catalog, digest


def backup(lake, destination, hook=None, fail_at=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "data").mkdir()
    events = []

    def note(phase, **fields):
        event = {"phase": phase, **fields}
        events.append(event)
        if hook is not None:
            hook(event)

    try:
        with lake.session() as c:
            c.execute("BEGIN")
            version = c.execute("SELECT version FROM head").fetchone()[0]
            entries = lake.entries(c, version)
            c.commit()
        note("backup.selected", version=version, objects=[e["object"] for e in entries])
        clone_catalog(lake.root / "catalog.db", destination / "catalog.db")
        note("backup.catalog")
        copied = []
        names = sorted({entry["object"] for entry in entries})
        expected = {entry["object"]: entry["digest"] for entry in entries}
        for index, name in enumerate(names):
            note("backup.before_file", index=index, object=name)
            if fail_at == index:
                raise OSError("injected copy failure")
            payload = (lake.data / name).read_bytes()
            if digest(payload) != expected[name]:
                raise ValueError("digest mismatch")
            (destination / "data" / name).write_bytes(payload)
            copied.append({"object": name, "digest": expected[name]})
            note("backup.after_file", index=index, object=name)
        receipt = {"version": version, "files": copied}
        (destination / "complete.json").write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
        note("backup.complete", version=version)
        return receipt
    except Exception as exc:
        events.append({"phase": "backup.failed", "error": type(exc).__name__})
        raise
    finally:
        (destination / "events.json").write_text(json.dumps(events, sort_keys=True), encoding="utf-8")


def restore(source, destination):
    source, destination = Path(source), Path(destination)
    receipt = json.loads((source / "complete.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "data").mkdir()
    clone_catalog(source / "catalog.db", destination / "catalog.db")
    for entry in receipt["files"]:
        payload = (source / "data" / entry["object"]).read_bytes()
        if digest(payload) != entry["digest"]:
            raise ValueError("digest mismatch")
        (destination / "data" / entry["object"]).write_bytes(payload)
    return Lake(destination)
