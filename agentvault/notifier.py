"""
AgentVault — alertes Discord.

Envoie des embeds colorés selon le type d'événement :
  - APPROVED  : vert  — transaction autorisée
  - DENIED    : rouge — transaction refusée
  - BUDGET    : orange — budget épuisé (< 10% restant)
  - CRITICAL  : rouge foncé — circuit breaker déclenché

Aucune exception ne remonte au caller : un échec Discord
ne doit jamais bloquer une transaction.
"""

import requests
from datetime import datetime


# Couleurs embed Discord (format décimal)
_COLOR = {
    "APPROVED": 0x2ECC71,   # vert
    "DENIED":   0xE74C3C,   # rouge
    "BUDGET":   0xE67E22,   # orange
    "CRITICAL": 0x8B0000,   # rouge foncé
}

# Timeout HTTP en secondes
_TIMEOUT = 5


class Notifier:
    """
    Envoie des notifications Discord via webhook.

    Args:
        webhook_url: URL complète du webhook Discord.
                     Si None ou vide, toutes les notifications sont silencieuses.
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or ""
        self._enabled = bool(self.webhook_url)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def approved(self, agent_name: str, amount: float, to: str,
                 reason: str, remaining: float) -> None:
        """Transaction autorisée."""
        self._send(
            event="APPROVED",
            title=f"✅ Transaction approuvée — {agent_name}",
            fields=[
                ("Montant",    f"{amount:.2f} USDC",  True),
                ("Vers",       _short_addr(to),        True),
                ("Restant",    f"{remaining:.2f} USDC", True),
                ("Motif",      reason,                  False),
            ],
        )

    def denied(self, agent_name: str, amount: float, to: str, reason: str) -> None:
        """Transaction refusée."""
        self._send(
            event="DENIED",
            title=f"🚫 Transaction refusée — {agent_name}",
            fields=[
                ("Montant demandé", f"{amount:.2f} USDC", True),
                ("Vers",            _short_addr(to),       True),
                ("Raison",          reason,                False),
            ],
        )

    def budget_warning(self, agent_name: str, remaining: float,
                       budget: float, period: str) -> None:
        """Budget de la période presque épuisé (< 10%)."""
        pct = (remaining / budget * 100) if budget > 0 else 0
        self._send(
            event="BUDGET",
            title=f"⚠️ Budget faible — {agent_name}",
            fields=[
                ("Restant",  f"{remaining:.2f} USDC ({pct:.1f}%)", True),
                ("Budget",   f"{budget:.2f} USDC / {period}",       True),
            ],
        )

    def circuit_breaker(self, agent_name: str, failures: int,
                        window_min: int) -> None:
        """Circuit breaker déclenché — intervention manuelle requise."""
        self._send(
            event="CRITICAL",
            title=f"🔴 CIRCUIT BREAKER — {agent_name}",
            fields=[
                ("Échecs",   str(failures),          True),
                ("Fenêtre",  f"{window_min} minutes", True),
                ("Action",   "Agent suspendu — intervention manuelle requise", False),
            ],
        )

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _send(self, event: str, title: str,
              fields: list[tuple[str, str, bool]]) -> None:
        """
        Construit et envoie un embed Discord.
        Silencieux si webhook non configuré ou si l'envoi échoue.
        """
        if not self._enabled:
            return

        embed = {
            "title": title,
            "color": _COLOR.get(event, 0x95A5A6),
            "fields": [
                {"name": name, "value": value, "inline": inline}
                for name, value, inline in fields
            ],
            "footer": {"text": f"AgentVault • {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"},
        }

        try:
            resp = requests.post(
                self.webhook_url,
                json={"embeds": [embed]},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception:
            # Échec Discord = silencieux, jamais de propagation
            pass


# ------------------------------------------------------------------
# Utilitaire
# ------------------------------------------------------------------

def _short_addr(addr: str) -> str:
    """Tronque une adresse Ethereum : 0x1234...abcd"""
    if len(addr) >= 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr
