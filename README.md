# AgentVault

**Budget controller open source pour agents IA dépensant de l'USDC.**

Les agents IA peuvent partir en boucle et brûler du budget sans garde-fous. AgentVault résout ça : chaque agent obtient un wallet USDC isolé sur Base (L2 Coinbase), avec des règles de dépense strictes appliquées *avant* chaque transaction.

```
Agent IA → wallet.authorize() → règles vérifiées → wallet.commit() → tx on-chain
                                      ↓ refus
                               alerte Discord
```

---

## Pourquoi AgentVault ?

| Problème | Solution |
|----------|----------|
| Agent en boucle → budget épuisé | Circuit breaker : N crashs en X minutes → arrêt automatique |
| Transaction vers adresse inconnue | Whitelist configurable par agent |
| Dépense incontrôlée sur la période | Budget semaine/mois avec plafond par transaction |
| Pas de visibilité | Alertes Discord : approuvé / refusé / budget épuisé |

**vs xpay.sh** (le concurrent principal) : TypeScript-only, SaaS, 1.5% de commission par transaction. AgentVault est Python natif, auto-hébergeable, zéro commission.

---

## Quickstart

### 1. Installation

```bash
git clone https://github.com/BigFoot3/agentvault
cd agentvault
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configuration

```bash
cp .env.example .env
# Éditer .env : AGENT_PRIVATE_KEY + DISCORD_WEBHOOK_URL
```

### 3. Utilisation

```python
from agentvault import AgentWallet
from agentvault.exceptions import BudgetExceeded

wallet = AgentWallet(
    agent_name="mon-agent-trading",
    budget_usdc=100,
    period="week",          # "day" | "week" | "month"
    max_per_tx=10,
    whitelist=["0xABC..."], # liste vide = toutes adresses autorisées
    discord_webhook="https://discord.com/api/webhooks/..."
)

# Pattern obligatoire : authorize → commit
auth = wallet.authorize(amount=5.0, to="0xABC...", reason="paiement API CoinGecko")
if auth.approved:
    wallet.commit(auth)
else:
    raise BudgetExceeded(auth.reason)
```

---

## Règles de dépense (dans l'ordre d'évaluation)

1. **Montant valide** — doit être > 0 USDC
2. **Circuit breaker** — agent suspendu si N crashs récents
3. **Whitelist** — adresse destinataire autorisée (si liste définie)
4. **Max par transaction** — plafond unitaire configurable
5. **Budget période** — cumul semaine/mois non dépassé

---

## Structure du projet

```
agentvault/
├── __init__.py       ← exports publics
├── wallet.py         ← AgentWallet : authorize() / commit()
├── rules.py          ← logique budget / whitelist / circuit breaker
├── notifier.py       ← alertes Discord
├── storage.py        ← persistance JSON (fcntl + écriture atomique)
└── exceptions.py     ← BudgetExceeded, WhitelistViolation, etc.
tests/
├── test_wallet.py
├── test_rules.py
└── test_storage.py
.env.example          ← template variables d'environnement
pyproject.toml
README.md
```

---

## Tests

```bash
source venv/bin/activate
pytest                         # 64+ tests, ~0.1s
pytest --cov=agentvault        # avec couverture
```

---

## Blockchain : Base (L2 Coinbase)

- **Frais** : < 0.001$ par transaction
- **USDC** : natif sur Base (Circle)
- **Testnet** : Base Sepolia — ETH et USDC de test gratuits via faucet
- **Faucets** :
  - ETH Sepolia : [faucet.quicknode.com](https://faucet.quicknode.com/base/sepolia)
  - USDC Sepolia : [faucet.circle.com](https://faucet.circle.com)

---

## Auto-hébergement sur VPS

Le projet tourne sur un Hetzner CX23 (3.59€/mois) avec les autres services. Aucune infrastructure supplémentaire requise — un fichier JSON par agent.

---

## Roadmap MVP

- [x] Logique budget / whitelist / circuit breaker (`rules.py`)
- [x] Persistance JSON thread-safe (`storage.py`)
- [ ] Alertes Discord (`notifier.py`)
- [ ] Orchestration `authorize() → commit()` (`wallet.py`)
- [ ] Création wallet USDC sur Base Sepolia
- [ ] Tests end-to-end testnet

## Post-MVP

- Version hébergée optionnelle (15-20€/mois, pas d'infra à gérer)
- Dashboard web minimal
- Support multi-agents

---

## Licence

MIT — libre d'utilisation, modification et redistribution.
