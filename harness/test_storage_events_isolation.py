from storage.events import publish, subscribe
from storage.principal import storage_principal


def test_storage_events_are_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("alice"):
        with subscribe("team") as alice_events:
            with storage_principal("bob"):
                with subscribe("team") as bob_events:
                    with storage_principal("alice"):
                        publish("team", "memory", "updated", "alice-only")
                    assert alice_events.get(timeout=0.1)
                    import queue
                    try:
                        bob_events.get(timeout=0.01)
                        assert False, "Bob received Alice's workspace event"
                    except queue.Empty:
                        pass
