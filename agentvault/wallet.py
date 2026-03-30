"""
AgentVault — AgentWallet.

Orchestre le pattern authorize() → commit() :
  1. authorize()  : vérifie les règles, retourne AuthResult (jamais d'exception)
  2. commit()     : exécute la tx on-chain via Chain, persiste, envoie notif Discord

Le wallet est stateless entre les appels : l'état est relu depuis le disque
à chaque authorize() pour être thread-safe en cas d'agents parallèles.
"""

import os
from datetime import datetime

from dotenv import load_dotenv

from .chain import Chain, TxReceipt
from .exceptions import CircuitBreakerTripped, StorageError
from .notifier import Notifier
from .rules import AuthResult, BudgetRules
from .storage import Storage

load_dotenv()

# Seuil d'alerte budget faible : 10% restant
_BUDGET_WARNING_PCT = 0.10


class AgentWallet:
    """
    Wallet USDC par agent avec guardrails de dépense.

    Args:
        agent_name:       Identifiant lisible de l'agent.
        budget_usdc:      Budget maximum sur la période.
        period:           "day" | "week" | "month".
        max_per_tx:       Plafond USDC par transaction (défaut : 10% du budget).
        whitelist:        Adresses autorisées (liste vide = toutes autorisées).
        discord_webhook:  URL webhook Discord. Si None, alertes désactivées.
        data_dir:         Répertoire de stockage JSON (défaut : ./data).
        onchain:          Si True, exécute les vraies tx on-chain via Chain.
                          Si False (défaut), mode dry-run — persiste sans envoyer.
        circuit_breaker_max:        Nombre de crashs avant suspension.
        circuit_breaker_window_min: Fenêtre glissante en minutes.

    Usage :
        wallet = AgentWallet(agent_name="mon-agent", budget_usdc=100, onchain=True)
        auth = wallet.authorize(amount=5.0, to="0xABC...", reason="API CoinGecko")
        if auth.approved:
            wallet.commit(auth)
        else:
            raise BudgetExceeded(auth.reason)
    """

    def __init__(
        self,
        agent_name: str,
        budget_usdc: float,
        period: str = "week",
        max_per_tx: float | None = None,
        whitelist: list[str] | None = None,
        discord_webhook: str | None = None,
        data_dir: str | None = None,
        onchain: bool = False,
        circuit_breaker_max: int = 5,
        circuit_breaker_window_min: int = 10,
    ) -> None:
        self.agent_name = agent_name
        self.budget_usdc = budget_usdc
        self.period = period
        self.onchain = onchain

        # max_per_tx défaut = 10% du budget
        self.max_per_tx = max_per_tx if max_per_tx is not None else round(budget_usdc * 0.10, 6)

        # Storage
        _data_dir = data_dir or os.getenv("AGENTVAULT_DATA_DIR", "./data")
        os.makedirs(_data_dir, exist_ok=True)
        safe_name = agent_name.replace("/", "_").replace(" ", "_")
        self._storage = Storage(os.path.join(_data_dir, f"{safe_name}.json"))

        # Règles
        self._rules = BudgetRules(
            budget_usdc=budget_usdc,
            period=period,
            max_per_tx=self.max_per_tx,
            whitelist=whitelist or [],
            circuit_breaker_max=circuit_breaker_max,
            circuit_breaker_window_min=circuit_breaker_window_min,
        )

        # Notifier
        webhook = discord_webhook or os.getenv("DISCORD_WEBHOOK_URL", "")
        self._notifier = Notifier(webhook)

        # Chain (optionnelle — initialisée seulement si onchain=True)
        self._chain: Chain | None = None
        if onchain:
            self._chain = Chain()

        # Initialise le fichier d'état si absent
        self._storage.init_if_absent(
            agent_name=agent_name,
            budget_usdc=budget_usdc,
            period=period,
            max_per_tx=self.max_per_tx,
            whitelist=whitelist or [],
        )

        mode = "ON-CHAIN" if onchain else "dry-run"
        print(
            f"[AgentVault] {agent_name} initialisé — "
            f"budget {budget_usdc} USDC/{period}, "
            f"max_per_tx {self.max_per_tx} USDC, "
            f"mode {mode}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def authorize(self, amount: float, to: str, reason: str = "") -> AuthResult:
        """
        Vérifie les règles de dépense et retourne un AuthResult.

        Ne lève jamais d'exception — inspecter auth.approved.
        Envoie une notification Discord si refusé.
        """
        now = datetime.utcnow()

        try:
            state = self._storage.load()
        except Exception as e:
            raise StorageError(f"Impossible de lire l'état de {self.agent_name} : {e}") from e

        result = self._rules.check(amount=amount, to=to, state=state, now=now)

        if not result.approved:
            self._notifier.denied(
                agent_name=self.agent_name,
                amount=amount,
                to=to,
                reason=result.reason,
            )
            print(f"[AgentVault] REFUSÉ {amount} USDC → {to} | {result.reason}", flush=True)
        else:
            print(
                f"[AgentVault] AUTORISÉ {amount} USDC → {to} | "
                f"auth_id={result.auth_id}",
                flush=True,
            )

        return result

    def commit(self, auth: AuthResult) -> TxReceipt | None:
        """
        Exécute la transaction et persiste l'état.

        Workflow :
          1. Vérifie auth.approved
          2. Si onchain=True → envoie la tx USDC sur Base via Chain
          3. Persiste la transaction dans le JSON
          4. Envoie la notification Discord

        Args:
            auth: AuthResult retourné par authorize() avec approved=True.

        Returns:
            TxReceipt si onchain=True, None en dry-run.

        Raises:
            ValueError:   Si auth.approved est False.
            RuntimeError: Si la tx on-chain échoue.
            StorageError: Si la persistance échoue.
        """
        if not auth.approved:
            raise ValueError(
                f"commit() appelé avec un AuthResult refusé : {auth.reason}"
            )

        now = datetime.utcnow()
        receipt: TxReceipt | None = None

        # --- Étape 1 : transaction on-chain (si activé) ---
        if self._chain is not None:
            receipt = self._chain.transfer_usdc(to=auth.to, amount_usdc=auth.amount)

        # --- Étape 2 : persistance ---
        try:
            state = self._storage.load()
        except Exception as e:
            raise StorageError(f"Impossible de lire l'état de {self.agent_name} : {e}") from e

        tx = {
            "auth_id":   auth.auth_id,
            "amount":    auth.amount,
            "to":        auth.to,
            "status":    "committed",
            "timestamp": now.isoformat(),
            "reason":    auth.reason,
            "tx_hash":   receipt.tx_hash if receipt else None,
            "gas_used":  receipt.gas_used if receipt else None,
            "onchain":   receipt is not None,
        }

        state = self._storage.record_transaction(state, tx)

        try:
            self._storage.save(state)
        except Exception as e:
            raise StorageError(
                f"Impossible de sauvegarder l'état de {self.agent_name} : {e}"
            ) from e

        # --- Étape 3 : notifications ---
        remaining = self._rules.remaining_budget(state, now)

        if remaining < self.budget_usdc * _BUDGET_WARNING_PCT:
            self._notifier.budget_warning(
                agent_name=self.agent_name,
                remaining=remaining,
                budget=self.budget_usdc,
                period=self.period,
            )

        self._notifier.approved(
            agent_name=self.agent_name,
            amount=auth.amount,
            to=auth.to,
            reason=auth.reason,
            remaining=remaining,
        )

        log = (
            f"[AgentVault] COMMIT {auth.amount} USDC → {auth.to} | "
            f"restant={remaining:.2f} USDC"
        )
        if receipt:
            log += f" | tx={receipt.tx_hash[:12]}..."
        print(log, flush=True)

        return receipt

    def record_failure(self) -> None:
        """
        Enregistre un crash de l'agent.
        Déclenche le circuit breaker si le seuil est atteint.

        Raises:
            CircuitBreakerTripped: Si le circuit breaker vient de se déclencher.
        """
        now = datetime.utcnow()

        try:
            state = self._storage.load()
            state = self._storage.record_failure(state, now)

            if self._rules.should_trip_circuit_breaker(state, now):
                state = self._storage.trip_circuit_breaker(state, now)
                self._storage.save(state)

                failures = len(state["circuit_breaker"]["failures"])
                self._notifier.circuit_breaker(
                    agent_name=self.agent_name,
                    failures=failures,
                    window_min=self._rules.cb_window,
                )
                print(
                    f"[AgentVault] ⚠️ CIRCUIT BREAKER — {self.agent_name}",
                    flush=True,
                )
                raise CircuitBreakerTripped(
                    f"Agent {self.agent_name} suspendu après {failures} échecs "
                    f"en {self._rules.cb_window} minutes"
                )

            self._storage.save(state)

        except CircuitBreakerTripped:
            raise
        except Exception as e:
            raise StorageError(f"record_failure échoué : {e}") from e

    def status(self) -> dict:
        """Résumé de l'état courant du wallet."""
        now = datetime.utcnow()
        state = self._storage.load()
        remaining = self._rules.remaining_budget(state, now)
        cb = state.get("circuit_breaker", {})

        result = {
            "agent_name":      self.agent_name,
            "budget_usdc":     self.budget_usdc,
            "period":          self.period,
            "max_per_tx":      self.max_per_tx,
            "remaining":       remaining,
            "spent":           round(self.budget_usdc - remaining, 6),
            "tx_count":        len(state.get("transactions", [])),
            "circuit_breaker": cb.get("tripped", False),
            "tripped_at":      cb.get("tripped_at"),
            "onchain":         self.onchain,
        }

        # Soldes on-chain si Chain disponible
        if self._chain:
            result["chain_usdc"] = self._chain.usdc_balance()
            result["chain_eth"]  = self._chain.eth_balance()

        return result
