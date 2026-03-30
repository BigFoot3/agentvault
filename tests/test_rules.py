"""
Tests — logique métier BudgetRules (rules.py).

Couverture :
  - AuthResult.raise_if_denied()
  - get_period_start() : day / week / month
  - compute_spent() : filtres status + période
  - BudgetRules.check() : toutes les règles dans l'ordre
  - BudgetRules.should_trip_circuit_breaker()
  - BudgetRules.remaining_budget()
  - Validation des paramètres du constructeur
"""

import pytest
from datetime import datetime, timedelta

from agentvault.rules import AuthResult, BudgetRules, compute_spent, get_period_start
from agentvault.exceptions import (
    BudgetExceeded,
    CircuitBreakerTripped,
    InvalidAmount,
    WhitelistViolation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WHITELIST = ["0xabc123", "0xdef456"]
NOW = datetime(2026, 3, 23, 14, 30, 0)  # Lundi 14h30 UTC


def make_rules(
    budget=100.0,
    period="week",
    max_per_tx=20.0,
    whitelist=None,
    cb_max=3,
    cb_window=10,
) -> BudgetRules:
    if whitelist is None:
        whitelist = WHITELIST
    return BudgetRules(
        budget_usdc=budget,
        period=period,
        max_per_tx=max_per_tx,
        whitelist=whitelist,
        circuit_breaker_max=cb_max,
        circuit_breaker_window_min=cb_window,
    )


def make_state(transactions=None, cb_failures=None, cb_tripped=False) -> dict:
    return {
        "transactions": transactions or [],
        "circuit_breaker": {
            "failures": cb_failures or [],
            "tripped": cb_tripped,
            "tripped_at": None,
        },
    }


def make_tx(amount: float, timestamp: datetime, status="committed") -> dict:
    return {
        "amount": amount,
        "timestamp": timestamp.isoformat(),
        "status": status,
        "to": "0xabc123",
        "reason": "test",
    }


# ---------------------------------------------------------------------------
# AuthResult
# ---------------------------------------------------------------------------

class TestAuthResult:
    def test_approved_does_not_raise(self):
        auth = AuthResult(approved=True, reason="ok", amount=5.0, to="0xabc")
        auth.raise_if_denied()  # doit être silencieux

    def test_denied_raises_budget_exceeded(self):
        auth = AuthResult(approved=False, reason="Budget épuisé", amount=5.0)
        with pytest.raises(BudgetExceeded):
            auth.raise_if_denied()

    def test_denied_raises_whitelist_violation(self):
        auth = AuthResult(approved=False, reason="Adresse non autorisée (whitelist active)")
        with pytest.raises(WhitelistViolation):
            auth.raise_if_denied()

    def test_denied_raises_circuit_breaker(self):
        auth = AuthResult(approved=False, reason="Circuit breaker déclenché")
        with pytest.raises(CircuitBreakerTripped):
            auth.raise_if_denied()

    def test_denied_raises_invalid_amount(self):
        auth = AuthResult(approved=False, reason="Montant invalide")
        with pytest.raises(InvalidAmount):
            auth.raise_if_denied()

    def test_auth_id_is_unique(self):
        a1 = AuthResult(approved=True, reason="ok")
        a2 = AuthResult(approved=True, reason="ok")
        assert a1.auth_id != a2.auth_id


# ---------------------------------------------------------------------------
# get_period_start
# ---------------------------------------------------------------------------

class TestGetPeriodStart:
    def test_day(self):
        result = get_period_start("day", NOW)
        assert result == datetime(2026, 3, 23, 0, 0, 0)

    def test_week_monday(self):
        # NOW est un lundi → début de semaine = lundi 00:00
        result = get_period_start("week", NOW)
        assert result == datetime(2026, 3, 23, 0, 0, 0)

    def test_week_friday(self):
        friday = datetime(2026, 3, 27, 10, 0, 0)  # vendredi
        result = get_period_start("week", friday)
        assert result == datetime(2026, 3, 23, 0, 0, 0)  # lundi précédent

    def test_week_sunday(self):
        sunday = datetime(2026, 3, 29, 23, 59, 0)  # dimanche
        result = get_period_start("week", sunday)
        assert result == datetime(2026, 3, 23, 0, 0, 0)  # lundi de la même semaine

    def test_month(self):
        result = get_period_start("month", NOW)
        assert result == datetime(2026, 3, 1, 0, 0, 0)

    def test_month_last_day(self):
        result = get_period_start("month", datetime(2026, 3, 31, 23, 59))
        assert result == datetime(2026, 3, 1, 0, 0, 0)

    def test_unknown_period_raises(self):
        with pytest.raises(ValueError, match="Période inconnue"):
            get_period_start("year", NOW)


# ---------------------------------------------------------------------------
# compute_spent
# ---------------------------------------------------------------------------

class TestComputeSpent:
    def test_empty_transactions(self):
        assert compute_spent([], NOW) == 0.0

    def test_counts_only_committed(self):
        period_start = datetime(2026, 3, 23, 0, 0, 0)
        txs = [
            make_tx(10.0, NOW, status="committed"),
            make_tx(5.0, NOW, status="pending"),    # ignoré
            make_tx(3.0, NOW, status="failed"),     # ignoré
        ]
        assert compute_spent(txs, period_start) == 10.0

    def test_counts_only_current_period(self):
        period_start = datetime(2026, 3, 23, 0, 0, 0)
        txs = [
            make_tx(10.0, datetime(2026, 3, 23, 1, 0)),   # cette semaine ✓
            make_tx(20.0, datetime(2026, 3, 16, 12, 0)),  # semaine passée ✗
            make_tx(5.0, datetime(2026, 3, 22, 23, 59)),  # dimanche passé ✗
        ]
        assert compute_spent(txs, period_start) == 10.0

    def test_counts_multiple_tx(self):
        period_start = datetime(2026, 3, 23, 0, 0, 0)
        txs = [
            make_tx(10.0, datetime(2026, 3, 23, 9, 0)),
            make_tx(7.5, datetime(2026, 3, 24, 15, 0)),
            make_tx(2.5, datetime(2026, 3, 25, 8, 0)),
        ]
        assert compute_spent(txs, period_start) == 20.0

    def test_ignores_malformed_timestamp(self):
        period_start = datetime(2026, 3, 23, 0, 0, 0)
        txs = [
            {"amount": 5.0, "timestamp": "not-a-date", "status": "committed"},
            make_tx(10.0, datetime(2026, 3, 24, 0, 0)),
        ]
        # Le tx malformé est ignoré, seul 10.0 est compté
        assert compute_spent(txs, period_start) == 10.0


# ---------------------------------------------------------------------------
# BudgetRules — constructeur
# ---------------------------------------------------------------------------

class TestBudgetRulesConstructor:
    def test_invalid_budget_raises(self):
        with pytest.raises(ValueError, match="budget_usdc"):
            BudgetRules(budget_usdc=0, period="week", max_per_tx=10, whitelist=[])

    def test_invalid_max_per_tx_raises(self):
        with pytest.raises(ValueError, match="max_per_tx"):
            BudgetRules(budget_usdc=100, period="week", max_per_tx=0, whitelist=[])

    def test_max_per_tx_exceeds_budget_raises(self):
        with pytest.raises(ValueError, match="max_per_tx"):
            BudgetRules(budget_usdc=50, period="week", max_per_tx=100, whitelist=[])

    def test_whitelist_normalized_to_lowercase(self):
        rules = make_rules(whitelist=["0xABC123", "0xDEF456"])
        assert "0xabc123" in rules.whitelist
        assert "0xdef456" in rules.whitelist


# ---------------------------------------------------------------------------
# BudgetRules.check() — règle 1 : montant valide
# ---------------------------------------------------------------------------

class TestCheckAmount:
    def test_zero_amount_rejected(self):
        rules = make_rules()
        result = rules.check(0.0, "0xabc123", make_state(), NOW)
        assert not result.approved
        assert "invalide" in result.reason.lower()

    def test_negative_amount_rejected(self):
        rules = make_rules()
        result = rules.check(-5.0, "0xabc123", make_state(), NOW)
        assert not result.approved

    def test_positive_amount_passes_this_rule(self):
        rules = make_rules()
        # 1.0 USDC, adresse whitelistée, budget dispo → doit être approuvé
        result = rules.check(1.0, "0xabc123", make_state(), NOW)
        assert result.approved


# ---------------------------------------------------------------------------
# BudgetRules.check() — règle 2 : circuit breaker
# ---------------------------------------------------------------------------

class TestCheckCircuitBreaker:
    def test_tripped_blocks_all_transactions(self):
        rules = make_rules()
        state = make_state(cb_tripped=True)
        result = rules.check(1.0, "0xabc123", state, NOW)
        assert not result.approved
        assert "circuit breaker" in result.reason.lower()

    def test_not_tripped_does_not_block(self):
        rules = make_rules()
        state = make_state(cb_tripped=False)
        result = rules.check(1.0, "0xabc123", state, NOW)
        assert result.approved


# ---------------------------------------------------------------------------
# BudgetRules.check() — règle 3 : whitelist
# ---------------------------------------------------------------------------

class TestCheckWhitelist:
    def test_address_in_whitelist_approved(self):
        rules = make_rules()
        result = rules.check(5.0, "0xabc123", make_state(), NOW)
        assert result.approved

    def test_address_case_insensitive(self):
        rules = make_rules()
        result = rules.check(5.0, "0xABC123", make_state(), NOW)
        assert result.approved

    def test_address_not_in_whitelist_rejected(self):
        rules = make_rules()
        result = rules.check(5.0, "0xUNKNOWN", make_state(), NOW)
        assert not result.approved
        assert "non autorisée" in result.reason.lower()

    def test_empty_whitelist_allows_all(self):
        rules = make_rules(whitelist=[])
        result = rules.check(5.0, "0xANYADDRESS", make_state(), NOW)
        assert result.approved


# ---------------------------------------------------------------------------
# BudgetRules.check() — règle 4 : max par transaction
# ---------------------------------------------------------------------------

class TestCheckMaxPerTx:
    def test_amount_equals_max_approved(self):
        rules = make_rules(max_per_tx=20.0)
        result = rules.check(20.0, "0xabc123", make_state(), NOW)
        assert result.approved

    def test_amount_exceeds_max_rejected(self):
        rules = make_rules(max_per_tx=20.0)
        result = rules.check(20.01, "0xabc123", make_state(), NOW)
        assert not result.approved
        assert "plafond" in result.reason.lower()

    def test_amount_below_max_approved(self):
        rules = make_rules(max_per_tx=20.0)
        result = rules.check(19.99, "0xabc123", make_state(), NOW)
        assert result.approved


# ---------------------------------------------------------------------------
# BudgetRules.check() — règle 5 : budget période
# ---------------------------------------------------------------------------

class TestCheckBudget:
    def test_full_budget_available(self):
        # 15 USDC < max_per_tx(20) et < budget(100) → approuvé
        rules = make_rules(budget=100.0)
        result = rules.check(15.0, "0xabc123", make_state(), NOW)
        assert result.approved

    def test_exactly_remaining_budget_approved(self):
        rules = make_rules(budget=100.0, max_per_tx=100.0)
        state = make_state(transactions=[make_tx(80.0, NOW)])
        result = rules.check(20.0, "0xabc123", state, NOW)
        assert result.approved

    def test_exceeds_remaining_budget_rejected(self):
        rules = make_rules(budget=100.0, max_per_tx=20.0)
        state = make_state(transactions=[make_tx(90.0, NOW)])
        result = rules.check(15.0, "0xabc123", state, NOW)
        assert not result.approved
        assert "budget" in result.reason.lower()

    def test_previous_period_tx_not_counted(self):
        """Tx de la semaine passée ne réduisent pas le budget courant."""
        rules = make_rules(budget=100.0)
        last_week_tx = make_tx(90.0, datetime(2026, 3, 16, 10, 0))
        state = make_state(transactions=[last_week_tx])
        # 15 USDC < max_per_tx(20), budget semaine courante = 100 entier → approuvé
        result = rules.check(15.0, "0xabc123", state, NOW)
        assert result.approved

    def test_approved_shows_remaining_budget(self):
        rules = make_rules(budget=100.0)
        state = make_state(transactions=[make_tx(30.0, NOW)])
        result = rules.check(10.0, "0xabc123", state, NOW)
        assert result.approved
        # 100 - 30 - 10 = 60 USDC restants
        assert "60.0" in result.reason or "60" in result.reason


# ---------------------------------------------------------------------------
# BudgetRules.should_trip_circuit_breaker()
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_no_failures_no_trip(self):
        rules = make_rules(cb_max=3, cb_window=10)
        state = make_state()
        assert not rules.should_trip_circuit_breaker(state, NOW)

    def test_below_threshold_no_trip(self):
        rules = make_rules(cb_max=3, cb_window=10)
        failures = [
            (NOW - timedelta(minutes=5)).isoformat(),
            (NOW - timedelta(minutes=3)).isoformat(),
        ]
        state = make_state(cb_failures=failures)
        assert not rules.should_trip_circuit_breaker(state, NOW)

    def test_at_threshold_trips(self):
        rules = make_rules(cb_max=3, cb_window=10)
        failures = [
            (NOW - timedelta(minutes=9)).isoformat(),
            (NOW - timedelta(minutes=5)).isoformat(),
            (NOW - timedelta(minutes=1)).isoformat(),
        ]
        state = make_state(cb_failures=failures)
        assert rules.should_trip_circuit_breaker(state, NOW)

    def test_old_failures_outside_window_ignored(self):
        rules = make_rules(cb_max=3, cb_window=10)
        failures = [
            (NOW - timedelta(minutes=15)).isoformat(),  # hors fenêtre
            (NOW - timedelta(minutes=20)).isoformat(),  # hors fenêtre
            (NOW - timedelta(minutes=5)).isoformat(),   # dans fenêtre
        ]
        state = make_state(cb_failures=failures)
        # Seulement 1 dans la fenêtre, seuil = 3 → pas de déclenchement
        assert not rules.should_trip_circuit_breaker(state, NOW)

    def test_already_tripped_returns_true(self):
        rules = make_rules()
        state = make_state(cb_tripped=True)
        assert rules.should_trip_circuit_breaker(state, NOW)

    def test_malformed_timestamps_ignored(self):
        rules = make_rules(cb_max=2, cb_window=10)
        failures = [
            "not-a-date",
            "also-invalid",
            (NOW - timedelta(minutes=1)).isoformat(),  # seul valide
        ]
        state = make_state(cb_failures=failures)
        assert not rules.should_trip_circuit_breaker(state, NOW)


# ---------------------------------------------------------------------------
# BudgetRules.remaining_budget()
# ---------------------------------------------------------------------------

class TestRemainingBudget:
    def test_no_spend_full_budget(self):
        rules = make_rules(budget=100.0)
        assert rules.remaining_budget(make_state(), NOW) == 100.0

    def test_partial_spend(self):
        rules = make_rules(budget=100.0)
        state = make_state(transactions=[
            make_tx(30.0, NOW),
            make_tx(15.0, NOW),
        ])
        assert rules.remaining_budget(state, NOW) == 55.0

    def test_previous_period_not_counted(self):
        rules = make_rules(budget=100.0)
        state = make_state(transactions=[
            make_tx(80.0, datetime(2026, 3, 15, 10, 0)),  # semaine passée
        ])
        assert rules.remaining_budget(state, NOW) == 100.0
