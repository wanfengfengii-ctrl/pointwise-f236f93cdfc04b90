import json
import sys
import tempfile
from pathlib import Path

from lake import Lake
from lab import exercise


def verify():
    samples = json.loads(Path(__file__).with_name("samples.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as root:
        lake = Lake(Path(root) / "lake")
        assert lake.commit(samples["initial"]) == 1
        assert lake.commit(samples["change"]) == 2
        assert lake.read(1) == [["north", "n1", 10], ["north", "n2", 0], ["south", "s1", 20]]
        assert lake.read() == [["north", "n3", 30], ["south", "s1", 20]]
    result = exercise({})
    assert result["complete"] is True
    assert result["declared_version"] == result["catalog_version"] == 1
    assert result["actual_rows"] == result["expected_rows"]
    failed = exercise({"fail_at": 1})
    assert failed["backup_error"] == "OSError"
    assert failed["complete"] is False
    assert failed["restore_error"] == "FileNotFoundError"
    print(json.dumps({"normal_backup": result["actual_rows"], "copy_failure": {
        "complete": failed["complete"], "error": failed["backup_error"]}}, ensure_ascii=False))


def repro():
    result = exercise({"at": {"backup.selected": [{"commit": "change"}]}})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if (result["complete"] and (result["catalog_version"] != result["declared_version"]
                               or result["actual_rows"] != result["expected_rows"])):
        print("BACKUP_RESTORE_MISMATCH")
        return 1
    raise AssertionError("expected backup inconsistency was not reproduced")


def demo():
    print(json.dumps(exercise({}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "verify":
        verify()
    elif mode == "repro":
        sys.exit(repro())
    elif mode == "demo":
        demo()
    else:
        raise SystemExit("use verify, repro or demo")
