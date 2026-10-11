"""Server-side, in-memory session data: only the latest dataset is kept, nothing goes to disk."""

import threading

from folioex.session import SessionRegistry


def test_put_get_clear() -> None:
    reg: SessionRegistry[str] = SessionRegistry()
    first = reg.put("one")
    assert reg.get(first) == "one"
    second = reg.put("two")
    assert reg.get(first) is None  # older dataset evicted
    assert reg.get(second) == "two"
    reg.clear()
    assert reg.get(second) is None
    assert reg.get(None) is None


def test_ids_are_unguessable_and_distinct() -> None:
    reg: SessionRegistry[int] = SessionRegistry()
    ids = {reg.put(i) for i in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 32 for i in ids)


def test_concurrent_puts_leave_one_consistent_dataset() -> None:
    reg: SessionRegistry[int] = SessionRegistry()
    ids: list[str] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        dataset_id = reg.put(n)
        with lock:
            ids.append(dataset_id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    alive = [i for i in ids if reg.get(i) is not None]
    assert len(alive) == 1
