import sqlite3

import pytest

from batch_engine.models import FAILED, SUCCEEDED, ResultRecord
from batch_engine.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "nested" / "jobs.db")
    s.create_job("j1", "sample.json", {"model": "m", "chunk_size": 10})
    return s


def ok(i):
    return ResultRecord(i, f"p{i}", SUCCEEDED, prompt=f"q{i}", response=f"a{i}", attempts=1, latency_ms=5)


def bad(i):
    return ResultRecord(i, f"p{i}", FAILED, error_type="server_error", error_message="boom", http_status=500, attempts=4)


def test_new_job_defaults(store):
    job = store.get_job("j1")
    assert job["status"] == "pending" and job["config"] == {"model": "m", "chunk_size": 10}
    assert (job["rows_read"], job["total"], job["succeeded"], job["failed"]) == (0, None, 0, 0)
    assert job["created_at"] and job["started_at"] is None


def test_unknown_job_is_none(store):
    assert store.get_job("nope") is None


def test_duplicate_job_id_rejected(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.create_job("j1", "x", {})


def test_lifecycle_updates(store):
    store.mark_running("j1")
    store.add_rows_read("j1", 10)
    store.add_rows_read("j1", 5)
    store.set_total("j1", 15)
    store.finish_job("j1", "completed")
    job = store.get_job("j1")
    assert (job["status"], job["rows_read"], job["total"]) == ("completed", 15, 15)
    assert job["started_at"] and job["finished_at"] and job["error"] is None


def test_write_results_updates_counters_atomically(store):
    store.write_results("j1", [ok(0), bad(1), ok(2)], retries_429=4, retries_5xx=3)
    job = store.get_job("j1")
    assert (job["succeeded"], job["failed"], job["retries_429"], job["retries_5xx"]) == (2, 1, 4, 3)


def test_upsert_overwrites_rows_instead_of_duplicating(store):
    store.write_results("j1", [bad(0)])
    store.write_results("j1", [ok(0)])
    assert [r["row_index"] for b in store.iter_results("j1", SUCCEEDED) for r in b] == [0]
    assert list(store.iter_results("j1", FAILED)) == []


def test_iter_results_batches_in_row_order_and_filters_by_status(store):
    records = [ok(i) if i % 3 else bad(i) for i in range(1000)]
    store.write_results("j1", list(reversed(records)))
    batches = list(store.iter_results("j1", SUCCEEDED, batch_size=100))
    rows = [r["row_index"] for b in batches for r in b]
    assert rows == [i for i in range(1000) if i % 3] and max(len(b) for b in batches) == 100
    assert batches[0][0] == {
        "row_index": 1, "item_id": "p1", "status": SUCCEEDED, "prompt": "q1", "response": "a1",
        "error_type": None, "error_message": None, "http_status": None, "attempts": 1, "latency_ms": 5,
    }
    assert len([r for b in store.iter_results("j1", FAILED) for r in b]) == 334


def test_results_are_isolated_per_job(store):
    store.create_job("j2", "x", {})
    store.write_results("j1", [ok(0)])
    store.write_results("j2", [ok(0), ok(1)])
    assert sum(len(b) for b in store.iter_results("j1", SUCCEEDED)) == 1


def test_mark_interrupted_only_touches_active_jobs(store):
    store.create_job("j2", "x", {})
    store.mark_running("j2")
    store.create_job("j3", "x", {})
    store.finish_job("j3", "completed")
    assert store.mark_interrupted() == 2
    assert [store.get_job(j)["status"] for j in ("j1", "j2", "j3")] == ["interrupted", "interrupted", "completed"]


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "jobs.db"
    Store(path).create_job("j", "x", {"a": 1})
    assert Store(path).get_job("j")["config"] == {"a": 1}
