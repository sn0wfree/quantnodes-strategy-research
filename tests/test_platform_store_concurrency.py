"""PlatformStore 并发写测试（F2 写线程锁）。

fanout 分支（跨线程）事件回流的前置护栏：全部公开方法串行化后，
多线程并发 emit/blob 不丢事件、不抛异常。
"""
from __future__ import annotations

import threading

from strategy_research.core.storage.platform_store import PlatformStore


def test_concurrent_emit_all_persisted(tmp_path):
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t1")
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(5):
                store.emit(session_id, "branch.settled", {"tid": tid, "i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.latest_seq(session_id) == 40
    events = store.replay(session_id, limit=100)
    assert len(events) == 40
    assert sorted({e.data["tid"] for e in events}) == list(range(8))
    store.close()


def test_concurrent_emit_and_blob_mixed(tmp_path):
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t2")
    errors: list[Exception] = []
    hashes: list[str] = []
    hash_lock = threading.Lock()

    def worker(tid: int) -> None:
        try:
            for i in range(4):
                store.emit(session_id, "branch.settled", {"tid": tid, "i": i})
                h = store.store_blob(session_id, f"blob-{tid}-{i}".encode())
                with hash_lock:
                    hashes.append(h)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.latest_seq(session_id) == 16
    # 全部 blob 可读且内容正确（内容寻址去重后仍逐条可验证）
    assert len(set(hashes)) == 16
    for tid in range(4):
        for i in range(4):
            h = __import__("hashlib").sha256(f"blob-{tid}-{i}".encode()).hexdigest()
            assert store.read_blob(session_id, h) == f"blob-{tid}-{i}".encode()
    store.close()


def test_cross_thread_emit_without_lock_crash_regression(tmp_path):
    """回归锁意图：单线程路径行为不变（emit 返回 seq 单调）。"""
    store = PlatformStore(data_dir=tmp_path)
    session_id = store.register_session("chat:t3")
    seqs = [store.emit(session_id, "e", {"n": i}).seq for i in range(3)]
    assert seqs == [1, 2, 3]
    store.close()
