"""
AgentVault — persistance JSON locale.

Pattern identique au crypto-agent (fcntl + écriture atomique via .tmp + os.replace).
Pas de base de données : un fichier JSON par agent.
"""

import fcntl
import json
import os
from datetime import datetime, timezone
from typing import Any

# État initial d'un agent — copié à la création du fichier
_DEFAULT_STATE: dict[str, Any] = {
    "agent_name": "",
    "budget_usdc": 0.0,
    "period": "week",
    "max_per_tx": 0.0,
    "whitelist": [],
    "spent_total": 0.0,          # cumulatif toutes périodes (informatif)
    "transactions": [],           # liste des tx commitées
    "circuit_breaker": {
        "failures": [],           # timestamps ISO des crashes récents
        "tripped": False,
        "tripped_at": None,
    },
    "created_at": None,
    "last_updated": None,
}


class Storage:
    """
    Lecture/écriture thread-safe du fichier d'état JSON d'un agent.

    - Lecture  : verrou partagé (LOCK_SH)
    - Écriture : verrou exclusif (LOCK_EX) + écriture atomique (.tmp → os.replace)
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._tmp = path + ".tmp"

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Charge l'état depuis le disque. Retourne l'état par défaut si absent."""
        if not os.path.exists(self.path):
            return self._fresh_state()

        with open(self.path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def save(self, state: dict[str, Any]) -> None:
        """Sauvegarde atomique : écrit dans .tmp, puis remplace le fichier cible."""
        state["last_updated"] = datetime.now(timezone.utc).isoformat()

        with open(self._tmp, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2, default=str, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        os.replace(self._tmp, self.path)

    def init_if_absent(self, agent_name: str, budget_usdc: float, period: str,
                       max_per_tx: float, whitelist: list[str]) -> dict[str, Any]:
        """
        Initialise le fichier d'état s'il n'existe pas encore.
        Retourne l'état chargé (existant ou nouveau).
        """
        if os.path.exists(self.path):
            return self.load()

        state = self._fresh_state()
        state.update({
            "agent_name": agent_name,
            "budget_usdc": budget_usdc,
            "period": period,
            "max_per_tx": max_per_tx,
            "whitelist": whitelist,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self.save(state)
        return state

    def record_transaction(self, state: dict[str, Any], tx: dict[str, Any]) -> dict[str, Any]:
        """
        Ajoute une transaction commitée à l'état et met à jour spent_total.
        Retourne l'état mis à jour (pas encore sauvegardé sur disque).
        """
        state["transactions"].append(tx)
        state["spent_total"] = round(state.get("spent_total", 0.0) + tx["amount"], 6)
        return state

    def record_failure(self, state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """
        Enregistre un timestamp d'échec dans le circuit breaker.
        Retourne l'état mis à jour.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        state["circuit_breaker"]["failures"].append(now.isoformat())
        return state

    def trip_circuit_breaker(self, state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """Déclenche le circuit breaker."""
        if now is None:
            now = datetime.now(timezone.utc)
        state["circuit_breaker"]["tripped"] = True
        state["circuit_breaker"]["tripped_at"] = now.isoformat()
        return state

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    @staticmethod
    def _fresh_state() -> dict[str, Any]:
        """Retourne une copie profonde de l'état initial."""
        import copy
        return copy.deepcopy(_DEFAULT_STATE)
