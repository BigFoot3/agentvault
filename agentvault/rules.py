"""
AgentVault — règles de dépense.

Logique pure, sans I/O.  Storage et Wallet utilisent ce module.

Règles évaluées dans l'ordre :
  1. Montant valide (> 0)
  2. Circuit breaker non déclenché
  3. Whitelist (si définie)
  4. Max par transaction
  5. Budget de la période courante
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .exceptions import BudgetExceeded, CircuitBreakerTripped, InvalidAmount, WhitelistViolation


# ---------------------------------------------------------------------------
# AuthResult — résultat d'une autorisation
# ---------------------------------------------------------------------------

@dataclass
class AuthResult:
    """Résultat retourné par BudgetRules.check()."""

    approved: bool
    reason: str
    amount: float = 0.0
    to: str = ""
    auth_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def raise_if_denied(self) -> None:
        """
        Lève l'exception métier appropriée si la transaction est refusée.
        Pratique dans les cas où le caller veut une exception plutôt qu'un booléen.
        """
        if self.approved:
            return

        reason_lower = self.reason.lower()
        if "circuit breaker" in reason_lower:
            raise CircuitBreakerTripped(self.reason)
        if "whitelist" in reason_lower or "non autorisée" in reason_lower:
            raise WhitelistViolation(self.reason)
        if "budget" in reason_lower or "restants" in reason_lower:
            raise BudgetExceeded(self.reason)
        raise InvalidAmount(self.reason)


# ---------------------------------------------------------------------------
# Helpers période
# ---------------------------------------------------------------------------

def get_period_start(period: str, now: datetime | None = None) -> datetime:
    """
    Retourne le début de la période courante (UTC).

    Périodes supportées : "day", "week" (lundi), "month".
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "week":
        # Lundi 00:00 UTC de la semaine courante
        monday = now - timedelta(days=now.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    raise ValueError(f"Période inconnue : '{period}'. Valeurs acceptées : day, week, month.")


def compute_spent(transactions: list[dict], period_start: datetime) -> float:
    """
    Calcule le total dépensé pendant la période courante.

    Seules les transactions avec status="committed" et timestamp >= period_start
    sont comptées.
    """
    total = 0.0
    for tx in transactions:
        if tx.get("status") != "committed":
            continue
        try:
            ts = datetime.fromisoformat(tx["timestamp"])
        except (KeyError, ValueError):
            continue
        if ts >= period_start:
            total += tx.get("amount", 0.0)
    return round(total, 6)


# ---------------------------------------------------------------------------
# BudgetRules — cœur de la logique
# ---------------------------------------------------------------------------

class BudgetRules:
    """
    Évalue les règles de dépense pour un agent donné.

    Instancié une fois dans AgentWallet et réutilisé à chaque authorize().
    Stateless : toute la persistance est dans le dict `state` passé en paramètre.

    Args:
        budget_usdc:                Budget max sur la période.
        period:                     "day" | "week" | "month".
        max_per_tx:                 Montant maximum par transaction.
        whitelist:                  Liste d'adresses autorisées (vide = toutes autorisées).
        circuit_breaker_max:        Nombre de crashs avant déclenchement.
        circuit_breaker_window_min: Fenêtre glissante en minutes.
    """

    def __init__(
        self,
        budget_usdc: float,
        period: str,
        max_per_tx: float,
        whitelist: list[str],
        circuit_breaker_max: int = 5,
        circuit_breaker_window_min: int = 10,
    ) -> None:
        if budget_usdc <= 0:
            raise ValueError(f"budget_usdc doit être > 0, reçu : {budget_usdc}")
        if max_per_tx <= 0:
            raise ValueError(f"max_per_tx doit être > 0, reçu : {max_per_tx}")
        if max_per_tx > budget_usdc:
            raise ValueError(
                f"max_per_tx ({max_per_tx}) ne peut pas dépasser budget_usdc ({budget_usdc})"
            )

        self.budget_usdc = budget_usdc
        self.period = period
        self.max_per_tx = max_per_tx
        # Normalisation en minuscules pour comparaison insensible à la casse
        self.whitelist: list[str] = [addr.lower() for addr in whitelist]
        self.cb_max = circuit_breaker_max
        self.cb_window = circuit_breaker_window_min

    # ------------------------------------------------------------------
    # API principale
    # ------------------------------------------------------------------

    def check(
        self,
        amount: float,
        to: str,
        state: dict[str, Any],
        now: datetime | None = None,
    ) -> AuthResult:
        """
        Évalue toutes les règles dans l'ordre et retourne un AuthResult.

        Ne lève jamais d'exception (utiliser AuthResult.raise_if_denied() si besoin).
        Stateless : ne modifie pas `state`.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # 1. Montant valide
        if amount <= 0:
            return AuthResult(
                approved=False,
                reason=f"Montant invalide : {amount} USDC (doit être > 0)",
                amount=amount,
                to=to,
            )

        # 2. Circuit breaker
        if state.get("circuit_breaker", {}).get("tripped"):
            return AuthResult(
                approved=False,
                reason="Circuit breaker déclenché — agent suspendu, intervention manuelle requise",
                amount=amount,
                to=to,
            )

        # 3. Whitelist (liste vide = pas de restriction)
        if self.whitelist and to.lower() not in self.whitelist:
            return AuthResult(
                approved=False,
                reason=f"Adresse non autorisée (whitelist active) : {to}",
                amount=amount,
                to=to,
            )

        # 4. Max par transaction
        if amount > self.max_per_tx:
            return AuthResult(
                approved=False,
                reason=(
                    f"Montant {amount} USDC dépasse le plafond par transaction "
                    f"({self.max_per_tx} USDC)"
                ),
                amount=amount,
                to=to,
            )

        # 5. Budget période
        period_start = get_period_start(self.period, now)
        spent = compute_spent(state.get("transactions", []), period_start)
        remaining = round(self.budget_usdc - spent, 6)

        if amount > remaining:
            return AuthResult(
                approved=False,
                reason=(
                    f"Budget {self.period} épuisé : {remaining:.2f} USDC restants "
                    f"sur {self.budget_usdc} USDC — demandé : {amount} USDC"
                ),
                amount=amount,
                to=to,
            )

        return AuthResult(
            approved=True,
            reason=(
                f"Approuvé — {remaining - amount:.2f} USDC restants "
                f"après transaction ({self.period})"
            ),
            amount=amount,
            to=to,
        )

    def should_trip_circuit_breaker(
        self,
        state: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        """
        Vérifie si le circuit breaker doit être déclenché.

        Compte les timestamps de crash dans la fenêtre glissante.
        Retourne True si le seuil est atteint ou dépassé.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        cb = state.get("circuit_breaker", {})

        # Déjà déclenché
        if cb.get("tripped"):
            return True

        failures = cb.get("failures", [])
        window_start = now - timedelta(minutes=self.cb_window)

        recent = [
            f for f in failures
            if _parse_iso(f) is not None and _parse_iso(f) >= window_start
        ]
        return len(recent) >= self.cb_max

    def remaining_budget(
        self,
        state: dict[str, Any],
        now: datetime | None = None,
    ) -> float:
        """Retourne le budget restant pour la période courante."""
        if now is None:
            now = datetime.now(timezone.utc)
        period_start = get_period_start(self.period, now)
        spent = compute_spent(state.get("transactions", []), period_start)
        return round(self.budget_usdc - spent, 6)


# ---------------------------------------------------------------------------
# Utilitaire interne
# ---------------------------------------------------------------------------

def _parse_iso(s: str) -> datetime | None:
    """Parse un timestamp ISO sans lever d'exception."""
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
