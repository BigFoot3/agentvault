"""
Tests — Storage (storage.py).

Couverture :
  - Chargement d'un fichier absent → état par défaut
  - Sauvegarde + rechargement
  - Écriture atomique (pas de fichier .tmp résiduel après save)
  - record_transaction()
  - record_failure() + trip_circuit_breaker()
  - init_if_absent() : création vs fichier existant
"""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from agentvault.storage import Storage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_path_file(tmp_path):
    """Retourne un chemin de fichier dans un répertoire temporaire."""
    return str(tmp_path / "agent_state.json")


@pytest.fixture
def storage(tmp_path_file):
    return Storage(tmp_path_file)


# ---------------------------------------------------------------------------
# load() — fichier absent
# ---------------------------------------------------------------------------

class TestLoadAbsent:
    def test_returns_default_state(self, storage):
        state = storage.load()
        assert state["transactions"] == []
        assert state["spent_total"] == 0.0
        assert state["circuit_breaker"]["tripped"] is False

    def test_does_not_create_file(self, storage, tmp_path_file):
        storage.load()
        assert not os.path.exists(tmp_path_file)


# ---------------------------------------------------------------------------
# save() + load()
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_roundtrip(self, storage):
        state = storage.load()
        state["agent_name"] = "test-agent"
        state["budget_usdc"] = 42.0
        storage.save(state)

        loaded = storage.load()
        assert loaded["agent_name"] == "test-agent"
        assert loaded["budget_usdc"] == 42.0

    def test_file_created_after_save(self, storage, tmp_path_file):
        state = storage.load()
        storage.save(state)
        assert os.path.exists(tmp_path_file)

    def test_no_tmp_file_residue(self, storage, tmp_path_file):
        state = storage.load()
        storage.save(state)
        assert not os.path.exists(tmp_path_file + ".tmp")

    def test_last_updated_set_on_save(self, storage):
        state = storage.load()
        storage.save(state)
        loaded = storage.load()
        assert loaded["last_updated"] is not None

    def test_valid_json_on_disk(self, storage, tmp_path_file):
        state = storage.load()
        storage.save(state)
        with open(tmp_path_file) as f:
            parsed = json.load(f)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# record_transaction()
# ---------------------------------------------------------------------------

class TestRecordTransaction:
    def test_adds_transaction(self, storage):
        state = storage.load()
        tx = {
            "auth_id": "abc-123",
            "amount": 5.0,
            "to": "0xabc",
            "status": "committed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        state = storage.record_transaction(state, tx)
        assert len(state["transactions"]) == 1
        assert state["transactions"][0]["amount"] == 5.0

    def test_updates_spent_total(self, storage):
        state = storage.load()
        state = storage.record_transaction(state, {"amount": 10.0, "status": "committed"})
        state = storage.record_transaction(state, {"amount": 5.5, "status": "committed"})
        assert state["spent_total"] == 15.5

    def test_does_not_save_automatically(self, storage, tmp_path_file):
        state = storage.load()
        storage.record_transaction(state, {"amount": 5.0})
        # record_transaction ne persiste pas automatiquement
        assert not os.path.exists(tmp_path_file)


# ---------------------------------------------------------------------------
# record_failure() + trip_circuit_breaker()
# ---------------------------------------------------------------------------

class TestCircuitBreakerStorage:
    def test_record_failure_adds_timestamp(self, storage):
        state = storage.load()
        now = datetime(2026, 3, 23, 10, 0, 0)
        state = storage.record_failure(state, now)
        assert len(state["circuit_breaker"]["failures"]) == 1
        assert "2026-03-23" in state["circuit_breaker"]["failures"][0]

    def test_record_failure_uses_utcnow_by_default(self, storage):
        state = storage.load()
        before = datetime.now(timezone.utc)
        state = storage.record_failure(state)
        after = datetime.now(timezone.utc)
        ts = datetime.fromisoformat(state["circuit_breaker"]["failures"][0])
        assert before <= ts <= after

    def test_trip_sets_tripped_true(self, storage):
        state = storage.load()
        state = storage.trip_circuit_breaker(state)
        assert state["circuit_breaker"]["tripped"] is True
        assert state["circuit_breaker"]["tripped_at"] is not None


# ---------------------------------------------------------------------------
# init_if_absent()
# ---------------------------------------------------------------------------

class TestInitIfAbsent:
    def test_creates_file_if_absent(self, storage, tmp_path_file):
        state = storage.init_if_absent("my-agent", 100.0, "week", 20.0, ["0xabc"])
        assert os.path.exists(tmp_path_file)
        assert state["agent_name"] == "my-agent"
        assert state["budget_usdc"] == 100.0

    def test_does_not_overwrite_existing(self, storage, tmp_path_file):
        # Premier init
        storage.init_if_absent("agent-v1", 100.0, "week", 20.0, [])
        # Deuxième appel : doit retourner l'existant sans écraser
        state = storage.init_if_absent("agent-v2", 999.0, "month", 50.0, [])
        assert state["agent_name"] == "agent-v1"
        assert state["budget_usdc"] == 100.0

    def test_created_at_set(self, storage):
        state = storage.init_if_absent("a", 10.0, "day", 5.0, [])
        assert state["created_at"] is not None
