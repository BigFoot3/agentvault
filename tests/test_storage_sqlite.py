"""
Tests — Storage SQLite (storage.py).

Même couverture qu'avant + tests spécifiques SQLite :
  - Schéma créé automatiquement
  - Concurrence (WAL mode)
  - get_transactions() / reset_circuit_breaker()
  - Compatibilité chemin .json → .db
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

from agentvault.storage import Storage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "agent.db")


@pytest.fixture
def storage(tmp_db):
    return Storage(tmp_db)


def make_tx(amount: float, timestamp: datetime, status="committed") -> dict:
    return {
        "auth_id":   "test-id",
        "amount":    amount,
        "to":        "0xabc123",
        "status":    status,
        "timestamp": timestamp.isoformat(),
        "reason":    "test",
        "tx_hash":   None,
        "gas_used":  None,
        "onchain":   False,
    }


NOW = datetime(2026, 3, 23, 14, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Schéma et init
# ---------------------------------------------------------------------------

class TestSchemaInit:
    def test_db_file_created_on_init(self, tmp_db):
        Storage(tmp_db)
        assert os.path.exists(tmp_db)

    def test_tables_exist(self, tmp_db):
        Storage(tmp_db)
        conn = sqlite3.connect(tmp_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert {"agent", "transactions", "circuit_breaker"}.issubset(tables)

    def test_json_path_converted_to_db(self, tmp_path):
        json_path = str(tmp_path / "agent.json")
        s = Storage(json_path)
        assert s.path.endswith(".db")
        assert os.path.exists(s.path)


# ---------------------------------------------------------------------------
# load() — fichier absent
# ---------------------------------------------------------------------------

class TestLoadAbsent:
    def test_returns_default_state(self, storage):
        state = storage.load()
        assert state["transactions"] == []
        assert state["spent_total"] == 0.0
        assert state["circuit_breaker"]["tripped"] is False

    def test_fresh_state_has_empty_whitelist(self, storage):
        state = storage.load()
        assert state["whitelist"] == []


# ---------------------------------------------------------------------------
# save() + load()
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_roundtrip_agent_config(self, storage):
        state = storage.load()
        state["agent_name"] = "test-agent"
        state["budget_usdc"] = 42.0
        state["whitelist"] = ["0xabc", "0xdef"]
        storage.save(state)

        loaded = storage.load()
        assert loaded["agent_name"] == "test-agent"
        assert loaded["budget_usdc"] == 42.0
        assert loaded["whitelist"] == ["0xabc", "0xdef"]

    def test_last_updated_set_on_save(self, storage):
        state = storage.load()
        storage.save(state)
        loaded = storage.load()
        assert loaded["last_updated"] is not None

    def test_transactions_roundtrip(self, storage):
        state = storage.load()
        tx = make_tx(5.0, NOW)
        state["transactions"].append(tx)
        storage.save(state)

        loaded = storage.load()
        assert len(loaded["transactions"]) == 1
        assert loaded["transactions"][0]["amount"] == 5.0

    def test_multiple_transactions_roundtrip(self, storage):
        state = storage.load()
        for amount in [1.0, 2.5, 3.0]:
            state["transactions"].append(make_tx(amount, NOW))
        storage.save(state)

        loaded = storage.load()
        amounts = [t["amount"] for t in loaded["transactions"]]
        assert sorted(amounts) == [1.0, 2.5, 3.0]

    def test_circuit_breaker_roundtrip(self, storage):
        state = storage.load()
        state["circuit_breaker"]["tripped"] = True
        state["circuit_breaker"]["tripped_at"] = NOW.isoformat()
        state["circuit_breaker"]["failures"] = [NOW.isoformat()]
        storage.save(state)

        loaded = storage.load()
        assert loaded["circuit_breaker"]["tripped"] is True
        assert loaded["circuit_breaker"]["tripped_at"] == NOW.isoformat()
        assert len(loaded["circuit_breaker"]["failures"]) == 1


# ---------------------------------------------------------------------------
# record_transaction()
# ---------------------------------------------------------------------------

class TestRecordTransaction:
    def test_adds_transaction(self, storage):
        state = storage.load()
        tx = make_tx(5.0, NOW)
        state = storage.record_transaction(state, tx)
        assert len(state["transactions"]) == 1

    def test_updates_spent_total(self, storage):
        state = storage.load()
        state = storage.record_transaction(state, make_tx(10.0, NOW))
        state = storage.record_transaction(state, make_tx(5.5, NOW))
        assert state["spent_total"] == 15.5

    def test_does_not_persist_automatically(self, storage):
        state = storage.load()
        storage.record_transaction(state, make_tx(5.0, NOW))
        # Sans save(), la DB ne doit pas avoir de tx
        reloaded = storage.load()
        assert len(reloaded["transactions"]) == 0


# ---------------------------------------------------------------------------
# record_failure() + trip_circuit_breaker()
# ---------------------------------------------------------------------------

class TestCircuitBreakerStorage:
    def test_record_failure_adds_timestamp(self, storage):
        state = storage.load()
        state = storage.record_failure(state, NOW)
        assert len(state["circuit_breaker"]["failures"]) == 1

    def test_trip_sets_tripped_true(self, storage):
        state = storage.load()
        state = storage.trip_circuit_breaker(state, NOW)
        assert state["circuit_breaker"]["tripped"] is True
        assert state["circuit_breaker"]["tripped_at"] is not None

    def test_record_failure_uses_utcnow_by_default(self, storage):
        state = storage.load()
        before = datetime.now(timezone.utc)
        state = storage.record_failure(state)
        after = datetime.now(timezone.utc)
        ts = datetime.fromisoformat(state["circuit_breaker"]["failures"][0])
        assert before <= ts <= after


# ---------------------------------------------------------------------------
# init_if_absent()
# ---------------------------------------------------------------------------

class TestInitIfAbsent:
    def test_creates_state_if_absent(self, storage):
        state = storage.init_if_absent("my-agent", 100.0, "week", 20.0, ["0xabc"])
        assert state["agent_name"] == "my-agent"
        assert state["budget_usdc"] == 100.0

    def test_does_not_overwrite_existing(self, storage):
        storage.init_if_absent("agent-v1", 100.0, "week", 20.0, [])
        state = storage.init_if_absent("agent-v2", 999.0, "month", 50.0, [])
        assert state["agent_name"] == "agent-v1"
        assert state["budget_usdc"] == 100.0

    def test_created_at_set(self, storage):
        state = storage.init_if_absent("a", 10.0, "day", 5.0, [])
        assert state["created_at"] is not None


# ---------------------------------------------------------------------------
# API étendue SQLite
# ---------------------------------------------------------------------------

class TestExtendedAPI:
    def test_get_transactions_empty(self, storage):
        assert storage.get_transactions() == []

    def test_get_transactions_returns_latest(self, storage):
        state = storage.init_if_absent("agent", 100.0, "week", 20.0, [])
        for amount in [1.0, 2.0, 3.0]:
            state = storage.record_transaction(state, make_tx(amount, NOW))
        storage.save(state)

        txs = storage.get_transactions(limit=2)
        assert len(txs) == 2
        # Les plus récentes en premier (ORDER BY id DESC)
        assert txs[0]["amount"] == 3.0

    def test_reset_circuit_breaker(self, storage):
        state = storage.init_if_absent("agent", 100.0, "week", 10.0, [])
        state = storage.trip_circuit_breaker(state, NOW)
        storage.save(state)

        storage.reset_circuit_breaker()
        loaded = storage.load()
        assert loaded["circuit_breaker"]["tripped"] is False
        assert loaded["circuit_breaker"]["tripped_at"] is None
        assert loaded["circuit_breaker"]["failures"] == []

    def test_wal_mode_enabled(self, tmp_db):
        """Vérifie que le mode WAL est activé (concurrence)."""
        s = Storage(tmp_db)
        conn = sqlite3.connect(tmp_db)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"
