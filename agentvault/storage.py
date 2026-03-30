"""
AgentVault — persistance SQLite.

Remplace le backend JSON tout en conservant exactement la même API publique :
  load() → dict
  save(state: dict) → None
  init_if_absent(...) → dict
  record_transaction(state, tx) → dict
  record_failure(state) → dict
  trip_circuit_breaker(state) → dict

Avantages vs JSON :
  - Concurrence native (WAL mode) → résout la race condition
  - Requêtes ciblées (pas de relecture complète à chaque authorize())
  - Pas de fcntl ni d'écriture atomique .tmp à gérer manuellement

Un fichier SQLite par agent : {data_dir}/{agent_name}.db
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Schéma — créé à l'init si absent
_DDL = """
CREATE TABLE IF NOT EXISTS agent (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    agent_name  TEXT    NOT NULL,
    budget_usdc REAL    NOT NULL,
    period      TEXT    NOT NULL,
    max_per_tx  REAL    NOT NULL,
    whitelist   TEXT    NOT NULL DEFAULT '[]',
    spent_total REAL    NOT NULL DEFAULT 0.0,
    created_at  TEXT,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    auth_id     TEXT,
    amount      REAL    NOT NULL,
    to_address  TEXT,
    status      TEXT    NOT NULL DEFAULT 'committed',
    timestamp   TEXT    NOT NULL,
    reason      TEXT,
    tx_hash     TEXT,
    gas_used    INTEGER,
    onchain     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    tripped     INTEGER NOT NULL DEFAULT 0,
    tripped_at  TEXT,
    failures    TEXT    NOT NULL DEFAULT '[]'
);
"""


class Storage:
    """
    Persistance SQLite par agent.

    Même API publique que l'ancienne version JSON — wallet.py ne change pas.

    Args:
        path: Chemin vers le fichier .db (ex: ./data/mon-agent.db)
    """

    def __init__(self, path: str) -> None:
        # Accepte les anciens chemins .json et les remplace par .db
        if path.endswith(".json"):
            path = path[:-5] + ".db"
        self.path = path
        self._ensure_schema()

    # ------------------------------------------------------------------
    # API publique (identique à l'ancienne version JSON)
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Charge l'état complet depuis SQLite. Retourne l'état par défaut si absent."""
        with self._connect() as conn:
            agent = conn.execute("SELECT * FROM agent WHERE id = 1").fetchone()
            if agent is None:
                return self._fresh_state()

            cb = conn.execute(
                "SELECT tripped, tripped_at, failures FROM circuit_breaker WHERE id = 1"
            ).fetchone()

            rows = conn.execute(
                "SELECT auth_id, amount, to_address, status, timestamp, "
                "reason, tx_hash, gas_used, onchain FROM transactions ORDER BY id"
            ).fetchall()

        transactions = [
            {
                "auth_id":    r[0],
                "amount":     r[1],
                "to":         r[2],
                "status":     r[3],
                "timestamp":  r[4],
                "reason":     r[5],
                "tx_hash":    r[6],
                "gas_used":   r[7],
                "onchain":    bool(r[8]),
            }
            for r in rows
        ]

        return {
            "agent_name":   agent["agent_name"],
            "budget_usdc":  agent["budget_usdc"],
            "period":       agent["period"],
            "max_per_tx":   agent["max_per_tx"],
            "whitelist":    json.loads(agent["whitelist"]),
            "spent_total":  agent["spent_total"],
            "transactions": transactions,
            "circuit_breaker": {
                "tripped":    bool(cb["tripped"]) if cb else False,
                "tripped_at": cb["tripped_at"] if cb else None,
                "failures":   json.loads(cb["failures"]) if cb else [],
            },
            "created_at":   agent["created_at"],
            "last_updated": agent["last_updated"],
        }

    def save(self, state: dict[str, Any]) -> None:
        """
        Sauvegarde l'état complet.

        Equivalent de l'ancienne écriture atomique .tmp → os.replace(),
        mais SQLite gère l'atomicité nativement en mode WAL.
        """
        now = datetime.now(timezone.utc).isoformat()
        state["last_updated"] = now
        cb = state.get("circuit_breaker", {})

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO agent (id, agent_name, budget_usdc, period, max_per_tx,
                                   whitelist, spent_total, created_at, last_updated)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    agent_name   = excluded.agent_name,
                    budget_usdc  = excluded.budget_usdc,
                    period       = excluded.period,
                    max_per_tx   = excluded.max_per_tx,
                    whitelist    = excluded.whitelist,
                    spent_total  = excluded.spent_total,
                    created_at   = excluded.created_at,
                    last_updated = excluded.last_updated
            """, (
                state.get("agent_name", ""),
                state.get("budget_usdc", 0.0),
                state.get("period", "week"),
                state.get("max_per_tx", 0.0),
                json.dumps(state.get("whitelist", [])),
                state.get("spent_total", 0.0),
                state.get("created_at"),
                now,
            ))

            # Resync transactions (simple: on vide et réinsère)
            conn.execute("DELETE FROM transactions")
            for tx in state.get("transactions", []):
                conn.execute("""
                    INSERT INTO transactions
                        (auth_id, amount, to_address, status, timestamp,
                         reason, tx_hash, gas_used, onchain)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tx.get("auth_id"),
                    tx.get("amount", 0.0),
                    tx.get("to"),
                    tx.get("status", "committed"),
                    tx.get("timestamp", now),
                    tx.get("reason"),
                    tx.get("tx_hash"),
                    tx.get("gas_used"),
                    int(tx.get("onchain", False)),
                ))

            conn.execute("""
                INSERT INTO circuit_breaker (id, tripped, tripped_at, failures)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tripped    = excluded.tripped,
                    tripped_at = excluded.tripped_at,
                    failures   = excluded.failures
            """, (
                int(cb.get("tripped", False)),
                cb.get("tripped_at"),
                json.dumps(cb.get("failures", [])),
            ))

    def init_if_absent(self, agent_name: str, budget_usdc: float, period: str,
                       max_per_tx: float, whitelist: list[str]) -> dict[str, Any]:
        """Initialise la DB si elle n'existe pas encore. Retourne l'état."""
        with self._connect() as conn:
            existing = conn.execute("SELECT id FROM agent WHERE id = 1").fetchone()
            if existing:
                return self.load()

        now = datetime.now(timezone.utc).isoformat()
        state = self._fresh_state()
        state.update({
            "agent_name":  agent_name,
            "budget_usdc": budget_usdc,
            "period":      period,
            "max_per_tx":  max_per_tx,
            "whitelist":   whitelist,
            "created_at":  now,
        })
        self.save(state)
        return state

    def record_transaction(self, state: dict[str, Any],
                           tx: dict[str, Any]) -> dict[str, Any]:
        """Ajoute une transaction à l'état (pas encore persisté)."""
        state["transactions"].append(tx)
        state["spent_total"] = round(state.get("spent_total", 0.0) + tx["amount"], 6)
        return state

    def record_failure(self, state: dict[str, Any],
                       now: datetime | None = None) -> dict[str, Any]:
        """Enregistre un timestamp d'échec."""
        if now is None:
            now = datetime.now(timezone.utc)
        state["circuit_breaker"]["failures"].append(now.isoformat())
        return state

    def trip_circuit_breaker(self, state: dict[str, Any],
                             now: datetime | None = None) -> dict[str, Any]:
        """Déclenche le circuit breaker."""
        if now is None:
            now = datetime.now(timezone.utc)
        state["circuit_breaker"]["tripped"] = True
        state["circuit_breaker"]["tripped_at"] = now.isoformat()
        return state

    # ------------------------------------------------------------------
    # API étendue (nouvelle — exploitée par la CLI)
    # ------------------------------------------------------------------

    def get_transactions(self, limit: int = 50) -> list[dict]:
        """Retourne les N dernières transactions directement depuis SQLite."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT auth_id, amount, to_address, status, timestamp,
                       reason, tx_hash, onchain
                FROM transactions
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [
            {
                "auth_id":   r[0],
                "amount":    r[1],
                "to":        r[2],
                "status":    r[3],
                "timestamp": r[4],
                "reason":    r[5],
                "tx_hash":   r[6],
                "onchain":   bool(r[7]),
            }
            for r in rows
        ]

    def reset_circuit_breaker(self) -> None:
        """Réinitialise le circuit breaker (usage CLI/admin)."""
        with self._connect() as conn:
            conn.execute("""
                UPDATE circuit_breaker
                SET tripped = 0, tripped_at = NULL, failures = '[]'
                WHERE id = 1
            """)

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL mode : lectures et écritures simultanées sans blocage
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    @staticmethod
    def _fresh_state() -> dict[str, Any]:
        import copy
        return copy.deepcopy({
            "agent_name":  "",
            "budget_usdc": 0.0,
            "period":      "week",
            "max_per_tx":  0.0,
            "whitelist":   [],
            "spent_total": 0.0,
            "transactions": [],
            "circuit_breaker": {
                "failures":   [],
                "tripped":    False,
                "tripped_at": None,
            },
            "created_at":  None,
            "last_updated": None,
        })

    def append_transaction(self, tx: dict[str, Any]) -> None:
        """Insert a single transaction directly — no full rewrite."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO transactions
                    (auth_id, amount, to_address, status, timestamp,
                     reason, tx_hash, gas_used, onchain)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx.get("auth_id"),
                tx.get("amount", 0.0),
                tx.get("to"),
                tx.get("status", "committed"),
                tx.get("timestamp"),
                tx.get("reason"),
                tx.get("tx_hash"),
                tx.get("gas_used"),
                int(tx.get("onchain", False)),
            ))
            conn.execute("""
                UPDATE agent SET spent_total = spent_total + ?, last_updated = ?
                WHERE id = 1
            """, (tx.get("amount", 0.0), tx.get("timestamp")))

    def save_meta(self, state: dict[str, Any]) -> None:
        """Save only agent config + circuit breaker — skip transactions table."""
        now = datetime.now(timezone.utc).isoformat()
        state["last_updated"] = now
        cb = state.get("circuit_breaker", {})

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO agent (id, agent_name, budget_usdc, period, max_per_tx,
                                   whitelist, spent_total, created_at, last_updated)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    agent_name   = excluded.agent_name,
                    budget_usdc  = excluded.budget_usdc,
                    period       = excluded.period,
                    max_per_tx   = excluded.max_per_tx,
                    whitelist    = excluded.whitelist,
                    spent_total  = excluded.spent_total,
                    created_at   = excluded.created_at,
                    last_updated = excluded.last_updated
            """, (
                state.get("agent_name", ""),
                state.get("budget_usdc", 0.0),
                state.get("period", "week"),
                state.get("max_per_tx", 0.0),
                json.dumps(state.get("whitelist", [])),
                state.get("spent_total", 0.0),
                state.get("created_at"),
                now,
            ))
            conn.execute("""
                INSERT INTO circuit_breaker (id, tripped, tripped_at, failures)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tripped    = excluded.tripped,
                    tripped_at = excluded.tripped_at,
                    failures   = excluded.failures
            """, (
                int(cb.get("tripped", False)),
                cb.get("tripped_at"),
                json.dumps(cb.get("failures", [])),
            ))
