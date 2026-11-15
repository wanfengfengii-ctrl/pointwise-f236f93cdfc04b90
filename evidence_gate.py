import json
import sys
from pathlib import Path

import checks
from lab import exercise


def replay():
    recipes = json.loads(Path("review_cases.json").read_text(encoding="utf-8"))
    if not isinstance(recipes, list) or not recipes:
        raise ValueError("review_cases must be a nonempty list")
    report = Path("REVIEW.md").read_text(encoding="utf-8")
    observed = []
    ids = set()
    for case in recipes:
        name = case["id"]
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError("case ids must be unique nonempty strings")
        ids.add(name)
        if "case:" + name not in report:
            raise AssertionError("case absent from report: " + name)
        if not case["locations"]:
            raise ValueError("source locations required")
        for loc in case["locations"]:
            if loc["path"] not in ("lake.py", "backup.py", "reclaim.py"):
                raise ValueError("not a production source")
            lines = Path(loc["path"]).read_text(encoding="utf-8").splitlines()
            if loc["line"] < 1 or lines[loc["line"] - 1].strip() != loc["text"]:
                raise AssertionError("stale source location")
        observed.append({"id": name, "result": exercise(case["spec"])})
    return observed


def verify():
    checks.verify()
    observed = replay()
    if not any(item["result"]["complete"] and (
        item["result"]["catalog_version"] != item["result"]["declared_version"]
        or item["result"]["actual_rows"] != item["result"]["expected_rows"]
    ) for item in observed):
        raise AssertionError("original inconsistent-success evidence missing")
    saved = json.loads(Path("review.json").read_text(encoding="utf-8"))
    if saved != observed:
        raise AssertionError("review.json differs from fresh source replay")
    print(json.dumps(observed, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "verify":
        verify()
    elif mode == "record":
        Path("review.json").write_text(json.dumps(replay(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif mode == "demo":
        print(json.dumps(replay(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit("use record, verify or demo")
