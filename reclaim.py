def plan(lake):
    with lake.session() as c:
        c.execute("BEGIN IMMEDIATE")
        version = c.execute("SELECT version FROM head").fetchone()[0]
        live = {entry["object"] for entry in lake.entries(c, version)}
        candidates = sorted(path.name for path in lake.data.glob("*.jsonl") if path.name not in live)
        c.commit()
    return candidates


def sweep(lake, candidates):
    removed = []
    for name in candidates:
        with lake.session() as c:
            c.execute("BEGIN IMMEDIATE")
            path = lake.data / name
            if path.exists():
                path.unlink()
                removed.append(name)
            c.commit()
    return removed


def collect(lake):
    return sweep(lake, plan(lake))
