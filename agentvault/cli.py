"""
AgentVault CLI

Usage:
    python -m agentvault.cli status   <agent_name>
    python -m agentvault.cli history  <agent_name> [--limit N]
    python -m agentvault.cli reset    <agent_name>
    python -m agentvault.cli init

Lit AGENTVAULT_DATA_DIR depuis .env (défaut : ./data).
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


def _data_dir() -> str:
    return os.getenv("AGENTVAULT_DATA_DIR", "./data")


def _get_storage(agent_name: str):
    from agentvault.storage import Storage
    safe = agent_name.replace("/", "_").replace(" ", "_")
    path = os.path.join(_data_dir(), f"{safe}.db")
    if not os.path.exists(path):
        print(f"❌  Agent '{agent_name}' introuvable dans {_data_dir()}/")
        sys.exit(1)
    return Storage(path)


# ------------------------------------------------------------------
# Commandes
# ------------------------------------------------------------------

def cmd_status(args) -> None:
    """Affiche l'état courant d'un agent."""
    from agentvault.rules import BudgetRules, get_period_start, compute_spent

    storage = _get_storage(args.agent_name)
    state = storage.load()

    now = datetime.now(timezone.utc)
    rules = BudgetRules(
        budget_usdc=state["budget_usdc"],
        period=state["period"],
        max_per_tx=state["max_per_tx"],
        whitelist=state["whitelist"],
    )
    period_start = get_period_start(state["period"], now)
    spent = compute_spent(state["transactions"], period_start)
    remaining = round(state["budget_usdc"] - spent, 6)
    cb = state["circuit_breaker"]

    print(f"\n{'─' * 44}")
    print(f"  AgentVault — {state['agent_name']}")
    print(f"{'─' * 44}")
    print(f"  Budget         {state['budget_usdc']:.2f} USDC / {state['period']}")
    print(f"  Dépensé        {spent:.2f} USDC")
    print(f"  Restant        {remaining:.2f} USDC")
    print(f"  Max / tx       {state['max_per_tx']:.2f} USDC")
    print(f"  Whitelist      {len(state['whitelist'])} adresse(s)")
    print(f"  Tx totales     {len(state['transactions'])}")

    cb_status = "🔴 DÉCLENCHÉ" if cb["tripped"] else "🟢 OK"
    print(f"  Circuit BK     {cb_status}")
    if cb["tripped"] and cb["tripped_at"]:
        print(f"  Déclenché le   {cb['tripped_at'][:19]} UTC")

    if state["created_at"]:
        print(f"  Créé le        {state['created_at'][:19]} UTC")
    print(f"{'─' * 44}\n")


def cmd_history(args) -> None:
    """Affiche les dernières transactions d'un agent."""
    storage = _get_storage(args.agent_name)
    txs = storage.get_transactions(limit=args.limit)

    if not txs:
        print(f"Aucune transaction pour '{args.agent_name}'.")
        return

    print(f"\n  Historique — {args.agent_name} (dernières {len(txs)})")
    print(f"  {'Date':<20} {'Montant':>8}  {'Statut':<10}  Raison")
    print(f"  {'─'*20} {'─'*8}  {'─'*10}  {'─'*30}")

    for tx in txs:
        date = tx["timestamp"][:16] if tx["timestamp"] else "?"
        amount = f"{tx['amount']:.2f}"
        status = tx["status"] or "?"
        reason = (tx["reason"] or "")[:40]
        chain_marker = " ⛓" if tx["onchain"] else ""
        print(f"  {date:<20} {amount:>8}  {status:<10}  {reason}{chain_marker}")
    print()


def cmd_reset(args) -> None:
    """Réinitialise le circuit breaker d'un agent."""
    storage = _get_storage(args.agent_name)
    state = storage.load()
    cb = state["circuit_breaker"]

    if not cb["tripped"]:
        print(f"ℹ️  Le circuit breaker de '{args.agent_name}' n'est pas déclenché.")
        return

    confirm = input(
        f"⚠️  Réinitialiser le circuit breaker de '{args.agent_name}' ? [oui/N] "
    ).strip().lower()

    if confirm != "oui":
        print("Annulé.")
        return

    storage.reset_circuit_breaker()
    print(f"✅  Circuit breaker de '{args.agent_name}' réinitialisé.")


def cmd_init(args) -> None:
    """Génère une nouvelle clé privée et un fichier .env."""
    try:
        from eth_account import Account
    except ImportError:
        print("❌  web3 requis : pip install web3")
        sys.exit(1)

    env_path = os.path.join(os.getcwd(), ".env")

    if os.path.exists(env_path):
        confirm = input(
            f"⚠️  Un fichier .env existe déjà ({env_path}). L'écraser ? [oui/N] "
        ).strip().lower()
        if confirm != "oui":
            print("Annulé.")
            return

    acc = Account.create()

    env_content = f"""# AgentVault — variables d'environnement
# Généré automatiquement par : python -m agentvault.cli init
# NE JAMAIS committer ce fichier dans Git

# Clé privée du wallet agent
AGENT_PRIVATE_KEY={acc.key.hex()}

# Webhook Discord pour les alertes (optionnel)
DISCORD_WEBHOOK_URL=

# Réseau blockchain : "base" (mainnet) ou "base-sepolia" (tests)
CHAIN=base-sepolia

# RPC Infura (optionnel — fallback si le RPC public est indisponible)
# INFURA_API_KEY=

# Répertoire de stockage des états (défaut : ./data)
# AGENTVAULT_DATA_DIR=./data
"""

    with open(env_path, "w") as f:
        f.write(env_content)

    print(f"\n✅  Wallet généré avec succès !")
    print(f"   Adresse    : {acc.address}")
    print(f"   .env       : {env_path}")
    print(f"\n⚠️  La clé privée est dans .env — ne la commitez jamais sur Git.")
    print(f"   Vérifiez que .env est dans votre .gitignore.\n")
    print(f"   Prochaines étapes :")
    print(f"   1. Obtenez de l'ETH sur Base pour payer le gas")
    print(f"   2. Obtenez de l'USDC sur Base")
    print(f"   3. Configurez DISCORD_WEBHOOK_URL pour les alertes\n")


# ------------------------------------------------------------------
# Entrée principale
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentvault",
        description="AgentVault — budget controller pour agents IA dépensant de l'USDC",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = sub.add_parser("status", help="Affiche l'état d'un agent")
    p_status.add_argument("agent_name", help="Nom de l'agent")
    p_status.set_defaults(func=cmd_status)

    # history
    p_history = sub.add_parser("history", help="Affiche l'historique des transactions")
    p_history.add_argument("agent_name", help="Nom de l'agent")
    p_history.add_argument("--limit", type=int, default=20,
                            help="Nombre de transactions à afficher (défaut : 20)")
    p_history.set_defaults(func=cmd_history)

    # reset
    p_reset = sub.add_parser("reset", help="Réinitialise le circuit breaker")
    p_reset.add_argument("agent_name", help="Nom de l'agent")
    p_reset.set_defaults(func=cmd_reset)

    # init
    p_init = sub.add_parser("init", help="Génère une clé privée et un .env")
    p_init.set_defaults(func=cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
