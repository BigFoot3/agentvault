"""
AgentVault — budget controller for AI agents spending USDC on Base.

Usage:

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
from .wallet import AgentWallet
from .chain import Chain, TxReceipt

__version__ = "0.1.0"
__all__ = [
    "AgentVaultError",
    "AgentWallet",
    "AuthResult",
    "BudgetExceeded",
    "BudgetRules",
    "Chain",
    "CircuitBreakerTripped",
    "InvalidAmount",
    "Storage",
    "StorageError",
    "TxReceipt",
    "WhitelistViolation",
    "compute_spent",
    "get_period_start",
]
