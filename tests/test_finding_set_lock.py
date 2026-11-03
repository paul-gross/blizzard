from unittest.mock import Mock

from blizzard.hub.store.internal.finding_set_lock import lock_finding_set_baselines


def test_baseline_lock_keys_preserve_all_three_identity_parts(monkeypatch):
    acquire = Mock()
    monkeypatch.setattr("blizzard.hub.store.internal.finding_set_lock.lock_keys", acquire)
    conn = Mock()
    lock_finding_set_baselines(conn, [("a", "b,c", "d"), ("a,b", "c", "d")])
    acquire.assert_called_once_with(conn, "finding-set-baseline", ['["a", "b,c", "d"]', '["a,b", "c", "d"]'])
