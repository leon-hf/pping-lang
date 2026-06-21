"""AppendLog(JSONL 追加+轮转) 与 JsonlStore(扫描查询) 测试。"""
from __future__ import annotations

from pping_lang.sink.metric_log import AppendLog, JsonlStore

# ── AppendLog ──────────────────────────────────────────────────────────────

def test_append_and_read_roundtrip(tmp_path):
    log = AppendLog(tmp_path / "m.jsonl")
    log.append([{"a": 1}, {"a": 2}])
    log.append([{"a": 3}])
    log.close()
    assert [r["a"] for r in AppendLog(tmp_path / "m.jsonl").read()] == [1, 2, 3]


def test_size_rotation_caps_disk_and_preserves_time_order(tmp_path):
    # huge volume_seconds + clock=0 → only the size cap triggers; keep 3 volumes
    p = tmp_path / "m.jsonl"
    log = AppendLog(p, max_bytes=200, volume_seconds=10**9, keep_volumes=3, clock=lambda: 0)
    for i in range(300):
        log.append([{"i": i}])
    log.close()
    seen = [r["i"] for r in AppendLog(p, keep_volumes=3, clock=lambda: 0).read()]
    # rotation drops oldest volumes → not all survive, but what survives is
    # contiguous, time-ordered, and the most recent record is always present
    assert seen == sorted(seen)
    assert seen[-1] == 299
    assert len(seen) < 300
    # disk bounded ~keep_volumes × max_bytes
    vols = [p, p.with_name("m.jsonl.1"), p.with_name("m.jsonl.2")]
    total = sum(f.stat().st_size for f in vols if f.exists())
    assert total <= 3 * 200 + 512  # +slack for straddling lines
    assert not p.with_name("m.jsonl.3").exists()  # never more than keep_volumes


def test_age_rotation_with_injected_clock(tmp_path):
    # each append jumps the clock past volume_seconds → rotation is age-driven,
    # never size-driven (max_bytes huge). keep 3 volumes → oldest records drop.
    p = tmp_path / "m.jsonl"
    ticks = [0]
    log = AppendLog(
        p, max_bytes=10**12, volume_seconds=10, keep_volumes=3, clock=lambda: ticks[0],
    )
    for i in range(6):
        ticks[0] += 11 * 10**9  # +11s each time, > 10s volume window
        log.append([{"i": i}])
    log.close()
    seen = [r["i"] for r in AppendLog(p, keep_volumes=3, clock=lambda: ticks[0]).read()]
    assert seen == sorted(seen)           # chronological
    assert 5 in seen                      # newest survives
    assert 0 not in seen                  # oldest aged out (time retention works)
    assert len(seen) < 6                  # bounded
    assert p.with_name("m.jsonl.1").exists()  # rotation happened without hitting size


def test_corrupt_line_skipped(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text('{"a":1}\nnot json\n{"a":2}\n', encoding="utf-8")
    assert [r["a"] for r in AppendLog(p).read()] == [1, 2]


def test_read_missing_file_is_empty(tmp_path):
    assert list(AppendLog(tmp_path / "nope.jsonl").read()) == []


# ── JsonlStore ─────────────────────────────────────────────────────────────

def _seed(tmp_path, points):
    """points: list of (ts_ns, name, value)."""
    from pping_lang.sink.metric_log import metrics_path
    log = AppendLog(metrics_path(tmp_path))
    log.append({"t": t, "e": 0, "g": -1, "n": n, "v": v, "l": None} for t, n, v in points)
    log.close()


def test_recent_metric_points_filters_name_and_window(tmp_path):
    _seed(tmp_path, [
        (10, "a", 1.0), (20, "b", 9.0), (30, "a", 2.0), (40, "a", 3.0),
    ])
    store = JsonlStore(tmp_path, "i")
    pts = store.recent_metric_points("a", since_ns=15)
    assert [(p["ts_ns"], p["value"]) for p in pts] == [(30, 2.0), (40, 3.0)]


def test_recent_metric_points_limit_keeps_newest(tmp_path):
    _seed(tmp_path, [(t, "a", float(t)) for t in range(1, 6)])
    store = JsonlStore(tmp_path, "i")
    pts = store.recent_metric_points("a", since_ns=0, limit=2)
    assert [p["ts_ns"] for p in pts] == [4, 5]  # newest 2, still chronological


def test_aggregate_metric(tmp_path):
    _seed(tmp_path, [(10, "a", 2.0), (20, "a", 4.0), (30, "a", 6.0)])
    store = JsonlStore(tmp_path, "i")
    assert store.aggregate_metric("a", 0, "avg") == 4.0
    assert store.aggregate_metric("a", 0, "max") == 6.0
    assert store.aggregate_metric("a", 0, "min") == 2.0
    assert store.aggregate_metric("a", 0, "sum") == 12.0
    assert store.aggregate_metric("a", 0, "count") == 3.0
    assert store.aggregate_metric("a", 0, "p50") == 4.0
    assert store.aggregate_metric("missing", 0, "avg") is None


def test_bucketed_quantiles(tmp_path):
    # two buckets over [0, 100): bucket0=[0,50) has 10,20 ; bucket1=[50,100) has 60
    _seed(tmp_path, [(10, "a", 10.0), (20, "a", 20.0), (60, "a", 60.0)])
    store = JsonlStore(tmp_path, "i")
    out = store.bucketed_quantiles("a", since_ns=0, until_ns=100, buckets=2)
    assert len(out) == 2
    assert out[0]["n"] == 2 and out[0]["avg"] == 15.0
    assert out[1]["n"] == 1 and out[1]["avg"] == 60.0


def test_list_instances_is_single(tmp_path):
    assert JsonlStore(tmp_path, "pod-9").list_instances() == ["pod-9"]
