"""
AgentVault — exceptions métier.
"""


class AgentVaultError(Exception):
    """Base exception AgentVault."""


class BudgetExceeded(AgentVaultError):
    """Budget de la période épuisé."""


class WhitelistViolation(AgentVaultError):
    """Adresse destinataire non autorisée."""


class CircuitBreakerTripped(AgentVaultError):
    """Circuit breaker déclenché — trop d'échecs consécutifs."""


class InvalidAmount(AgentVaultError):
    """Montant invalide (≤ 0 ou dépasse max_per_tx)."""


class StorageError(AgentVaultError):
    """Erreur lecture/écriture du fichier d'état."""
