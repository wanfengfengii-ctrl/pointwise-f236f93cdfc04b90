import json
import tempfile
from pathlib import Path

import backup
import reclaim
from lake import Lake


SAMPLES = Path(__file__).with_name("samples.json")


def exercise(spec):
    batches = json.loads(SAMPLES.read_text(encoding="utf-8"))
    state = {}
    expected_versions = {0: []}
    actions = []
    pending = None
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        lake = Lake(root / "source")

        def commit(label):
            batch = batches[label]
            for part, rows in batch.items():
                if rows:
                    state[part] = [list(row) for row in rows]
                else:
                    state.pop(part, None)
            version = lake.commit(batch)
            expected_versions[version] = sorted([part, *row] for part, rows in state.items() for row in rows)
            actions.append({"action": "commit", "batch": label, "version": version})

        def apply(sequence):
            nonlocal pending
            for action in sequence:
                if "commit" in action:
                    commit(action["commit"])
                elif action == {"gc": "plan"}:
                    pending = reclaim.plan(lake)
                    actions.append({"action": "gc.plan", "objects": pending})
                elif action == {"gc": "sweep"}:
                    if pending is None:
                        raise ValueError("sweep without plan")
                    removed = reclaim.sweep(lake, pending)
                    actions.append({"action": "gc.sweep", "removed": removed})
                    pending = None
                elif action == {"gc": "collect"}:
                    removed = reclaim.collect(lake)
                    actions.append({"action": "gc.collect", "removed": removed})
                else:
                    raise ValueError("unknown action")

        commit("initial")
        apply(spec.get("before", []))
        selected = lake.version()
        expected = expected_versions[selected]

        def hook(event):
            actions.append(dict(event))
            key = event["phase"]
            if "index" in event:
                key += ":" + str(event["index"])
            apply(spec.get("at", {}).get(key, []))

        target = root / "backup"
        receipt = None
        backup_error = None
        try:
            receipt = backup.backup(lake, target, hook, spec.get("fail_at"))
        except Exception as exc:
            backup_error = type(exc).__name__
        complete = (target / "complete.json").exists()
        catalog_version = None
        actual = None
        restore_error = None
        if complete:
            try:
                restored = backup.restore(target, root / "restored")
                catalog_version = restored.version()
                actual = restored.read()
            except Exception as exc:
                restore_error = type(exc).__name__
        else:
            try:
                backup.restore(target, root / "restored")
            except Exception as exc:
                restore_error = type(exc).__name__
        return {
            "selected_version": selected,
            "declared_version": None if receipt is None else receipt["version"],
            "catalog_version": catalog_version,
            "expected_rows": expected,
            "actual_rows": actual,
            "complete": complete,
            "backup_error": backup_error,
            "restore_error": restore_error,
            "events": actions,
            "backup_events": json.loads((target / "events.json").read_text(encoding="utf-8")),
        }
