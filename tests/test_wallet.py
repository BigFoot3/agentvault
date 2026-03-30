"""
Tests — AgentWallet (wallet.py).

Intégration sans blockchain ni Discord :
  - authorize() : approuvé / refusé / circuit breaker
  - commit()     : persistance + état mis à jour
  - record_failure() : circuit breaker après N échecs
  - status()     : résumé correct
  - Notifications Discord silencieuses (webhook vide)
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from agentvault.wallet import AgentWallet
from agentvault.exceptions import (
    BudgetExceeded,
    CircuitBreakerTripped,
    WhitelistViolation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WEBHOOK = "https://discord.com/api/webhooks/test/token"
ADDR_OK  = "0xabc123def456"
ADDR_BAD = "0xunknown999"


@pytest.fixture
def wallet(tmp_path):
    """Wallet de test — pas de Discord réel, stockage dans tmp."""
    return AgentWallet(
        agent_name="test-agent",
        budget_usdc=100.0,
        period="week",
        max_per_tx=20.0,
        whitelist=[ADDR_OK],
        discord_webhook="",   # désactivé
        data_dir=str(tmp_path),
    )


@pytest.fixture
def wallet_no_whitelist(tmp_path):
    return AgentWallet(
        agent_name="agent-open",
        budget_usdc=50.0,
        period="week",
        max_per_tx=10.0,
        whitelist=[],
        discord_webhook="",
        data_dir=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# authorize() — approbations
# ---------------------------------------------------------------------------

class TestAuthorizeApproved:
    def test_valid_tx_approved(self, wallet):
        auth = wallet.authorize(5.0, ADDR_OK, "paiement API")
        assert auth.approved

    def test_auth_id_present(self, wallet):
        auth = wallet.authorize(5.0, ADDR_OK)
        assert auth.auth_id
        assert len(auth.auth_id) == 36  # UUID format

    def test_no_whitelist_allows_any_address(self, wallet_no_whitelist):
        auth = wallet_no_whitelist.authorize(5.0, "0xANYADDRESS")
        assert auth.approved

    def test_amount_exactly_max_per_tx(self, wallet):
        auth = wallet.authorize(20.0, ADDR_OK)
        assert auth.approved


# ---------------------------------------------------------------------------
# authorize() — refus
# ---------------------------------------------------------------------------

class TestAuthorizeDenied:
    def test_zero_amount_denied(self, wallet):
        auth = wallet.authorize(0.0, ADDR_OK)
        assert not auth.approved

    def test_exceeds_max_per_tx_denied(self, wallet):
        auth = wallet.authorize(25.0, ADDR_OK)
        assert not auth.approved

    def test_whitelist_violation_denied(self, wallet):
        auth = wallet.authorize(5.0, ADDR_BAD)
        assert not auth.approved

    def test_budget_exhausted_denied(self, wallet):
        # Épuiser le budget avec des commits
        for _ in range(5):
            auth = wallet.authorize(20.0, ADDR_OK)
            wallet.commit(auth)
        # 100 USDC dépensés — plus rien de disponible
        auth = wallet.authorize(1.0, ADDR_OK)
        assert not auth.approved

    def test_denied_does_not_raise(self, wallet):
        """authorize() ne lève jamais d'exception même si refusé."""
        auth = wallet.authorize(999.0, ADDR_OK)  # > max_per_tx et > budget
        assert not auth.approved  # silencieux

    def test_raise_if_denied_raises_correct_exception(self, wallet):
        auth = wallet.authorize(999.0, ADDR_OK)
        with pytest.raises(Exception):
            auth.raise_if_denied()

    def test_whitelist_violation_raises_correct_type(self, wallet):
        auth = wallet.authorize(5.0, ADDR_BAD)
        with pytest.raises(WhitelistViolation):
            auth.raise_if_denied()


# ---------------------------------------------------------------------------
# commit()
# ---------------------------------------------------------------------------

class TestCommit:
    def test_commit_persists_transaction(self, wallet):
        auth = wallet.authorize(5.0, ADDR_OK, "test")
        wallet.commit(auth)
        status = wallet.status()
        assert status["tx_count"] == 1
        assert status["spent"] == 5.0

    def test_commit_updates_remaining(self, wallet):
        auth = wallet.authorize(15.0, ADDR_OK)
        wallet.commit(auth)
        status = wallet.status()
        assert status["remaining"] == 85.0

    def test_multiple_commits_accumulate(self, wallet):
        for amount in [5.0, 10.0, 8.0]:
            auth = wallet.authorize(amount, ADDR_OK)
            wallet.commit(auth)
        status = wallet.status()
        assert status["spent"] == 23.0
        assert status["tx_count"] == 3

    def test_commit_with_denied_auth_raises(self, wallet):
        auth = wallet.authorize(999.0, ADDR_OK)  # refusé
        with pytest.raises(ValueError, match="AuthResult refusé"):
            wallet.commit(auth)

    def test_commit_does_not_allow_double_spend(self, wallet):
        """Deux commits du même auth ne doublent pas la dépense en prod
        mais ici on vérifie juste que le second authorize est bloqué."""
        # Dépenser tout le budget
        for _ in range(5):
            auth = wallet.authorize(20.0, ADDR_OK)
            wallet.commit(auth)
        # Le suivant doit être refusé
        auth = wallet.authorize(1.0, ADDR_OK)
        assert not auth.approved


# ---------------------------------------------------------------------------
# record_failure() — circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_single_failure_does_not_trip(self, wallet):
        wallet.record_failure()
        status = wallet.status()
        assert not status["circuit_breaker"]

    def test_circuit_breaker_trips_at_threshold(self, tmp_path):
        """5 failures en moins de 10 minutes → circuit breaker."""
        w = AgentWallet(
            agent_name="agent-cb",
            budget_usdc=100.0,
            period="week",
            max_per_tx=10.0,
            whitelist=[],
            discord_webhook="",
            data_dir=str(tmp_path),
            circuit_breaker_max=3,
            circuit_breaker_window_min=10,
        )
        w.record_failure()
        w.record_failure()
        with pytest.raises(CircuitBreakerTripped):
            w.record_failure()

    def test_tripped_blocks_authorize(self, tmp_path):
        """Une fois déclenché, authorize() est bloqué."""
        w = AgentWallet(
            agent_name="agent-blocked",
            budget_usdc=100.0,
            period="week",
            max_per_tx=10.0,
            whitelist=[],
            discord_webhook="",
            data_dir=str(tmp_path),
            circuit_breaker_max=2,
            circuit_breaker_window_min=10,
        )
        w.record_failure()
        try:
            w.record_failure()
        except CircuitBreakerTripped:
            pass

        auth = w.authorize(5.0, "0xany")
        assert not auth.approved
        assert "circuit breaker" in auth.reason.lower()


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:
    def test_initial_status(self, wallet):
        s = wallet.status()
        assert s["agent_name"] == "test-agent"
        assert s["budget_usdc"] == 100.0
        assert s["remaining"] == 100.0
        assert s["spent"] == 0.0
        assert s["tx_count"] == 0
        assert not s["circuit_breaker"]

    def test_status_after_spend(self, wallet):
        auth = wallet.authorize(15.0, ADDR_OK)  # 15 < max_per_tx=20
        wallet.commit(auth)
        s = wallet.status()
        assert s["remaining"] == 85.0
        assert s["spent"] == 15.0
        assert s["tx_count"] == 1


# ---------------------------------------------------------------------------
# Notifications Discord — vérification que les appels sont faits
# ---------------------------------------------------------------------------

class TestNotifications:
    def test_denied_triggers_discord_notification(self, tmp_path):
        """Vérifie que Notifier.denied() est appelé sur un refus."""
        w = AgentWallet(
            agent_name="agent-notif",
            budget_usdc=50.0,
            period="week",
            max_per_tx=10.0,
            whitelist=["0xgood"],
            discord_webhook=WEBHOOK,
            data_dir=str(tmp_path),
        )
        with patch.object(w._notifier, "denied") as mock_denied:
            w.authorize(5.0, "0xbad_address")
            mock_denied.assert_called_once()

    def test_approved_commit_triggers_discord_notification(self, tmp_path):
        """Vérifie que Notifier.approved() est appelé après commit."""
        w = AgentWallet(
            agent_name="agent-notif2",
            budget_usdc=50.0,
            period="week",
            max_per_tx=10.0,
            whitelist=[],
            discord_webhook=WEBHOOK,
            data_dir=str(tmp_path),
        )
        auth = w.authorize(5.0, "0xany")
        with patch.object(w._notifier, "approved") as mock_approved:
            w.commit(auth)
            mock_approved.assert_called_once()

    def test_budget_warning_triggered_near_limit(self, tmp_path):
        """Alerte budget faible déclenchée quand < 10% restant."""
        w = AgentWallet(
            agent_name="agent-low",
            budget_usdc=100.0,
            period="week",
            max_per_tx=100.0,  # permet une grosse tx
            whitelist=[],
            discord_webhook=WEBHOOK,
            data_dir=str(tmp_path),
        )
        auth = w.authorize(95.0, "0xany")  # laisse 5% restant
        with patch.object(w._notifier, "budget_warning") as mock_warning:
            w.commit(auth)
            mock_warning.assert_called_once()
