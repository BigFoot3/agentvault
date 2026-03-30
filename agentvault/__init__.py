"""
AgentVault — budget controller open source pour agents IA dépensant de l'USDC.

Usage minimal :

    from agentvault import AgentWallet
    from agentvault.exceptions import BudgetExceeded
"""

from .exceptions import (
    AgentVaultError,
    BudgetExceeded,
    CircuitBreakerTripped,
    InvalidAmount,
    StorageError,
    WhitelistViolation,
)
from .rules import AuthResult, BudgetRules, compute_spent, get_period_start
from .storage import Storage

__version__ = "0.1.0"
__all__ = [
    "AgentVaultError",
    "AuthResult",
    "BudgetExceeded",
    "BudgetRules",
    "CircuitBreakerTripped",
    "InvalidAmount",
    "Storage",
    "StorageError",
    "WhitelistViolation",
    "compute_spent",
    "get_period_start",
]
