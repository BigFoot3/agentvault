# AgentVault

**Budget controller for AI agents spending USDC on Base.**

AI agents can loop indefinitely and burn through budgets without guardrails. AgentVault fixes this: each agent gets an isolated USDC wallet on Base (Coinbase L2), with strict spending rules enforced *before* every transaction.

```
Agent → wallet.authorize() → rules checked → wallet.commit() → on-chain tx
                                   ↓ denied
                            Discord alert
```

---

## Why AgentVault?

| Problem | Solution |
|---------|----------|
| Agent loops → budget drained | Circuit breaker: N crashes in X minutes → automatic suspension |
| Transaction to unknown address | Configurable whitelist per agent |
| Uncontrolled spending | Weekly/monthly budget with per-transaction cap |
| No visibility | Discord alerts: approved / denied / low budget |

**vs xpay.sh** (main competitor): TypeScript-only, SaaS, 1.5% commission per transaction. AgentVault is Python-native, self-hostable, zero commission.

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/BigFoot3/agentvault
cd agentvault
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### 2. Generate a wallet

```bash
python -m agentvault.cli init
```

This creates a `.env` file with a fresh private key and wallet address. Fund the wallet with ETH (for gas) and USDC on Base.

### 3. Use in your agent

```python
from agentvault import AgentWallet
from agentvault.exceptions import BudgetExceeded

wallet = AgentWallet(
    agent_name="my-trading-agent",
    budget_usdc=100,
    period="week",          # "day" | "week" | "month"
    max_per_tx=10,
    whitelist=["0xABC..."], # empty list = all addresses allowed
    onchain=True,           # False = dry-run (no real tx)
)

# Mandatory pattern: authorize → commit
auth = wallet.authorize(amount=5.0, to="0xABC...", reason="CoinGecko API payment")
if auth.approved:
    wallet.commit(auth)
else:
    raise BudgetExceeded(auth.reason)
```

---

## Spending rules (evaluated in order)

1. **Valid amount** — must be > 0 USDC
2. **Circuit breaker** — agent suspended if N recent crashes
3. **Whitelist** — destination address authorized (if list defined)
4. **Max per transaction** — configurable per-transaction cap
5. **Period budget** — weekly/monthly cumulative not exceeded

---

## CLI

```bash
# Check agent status
python -m agentvault.cli status my-agent

# View transaction history
python -m agentvault.cli history my-agent --limit 50

# Reset circuit breaker (after fixing the underlying issue)
python -m agentvault.cli reset my-agent

# Generate a new private key and .env
python -m agentvault.cli init
```

---

## Project structure

```
agentvault/
├── __init__.py       ← public exports
├── wallet.py         ← AgentWallet: authorize() / commit()
├── rules.py          ← budget / whitelist / circuit breaker logic
├── notifier.py       ← Discord alerts
├── storage.py        ← SQLite persistence (WAL mode, concurrent-safe)
├── chain.py          ← real USDC transfers on Base via web3.py
├── cli.py            ← CLI: status / history / reset / init
└── exceptions.py     ← BudgetExceeded, WhitelistViolation, etc.
tests/
├── test_wallet.py
├── test_rules.py
├── test_storage.py
└── test_chain.py
.env.example
pyproject.toml
```

---

## Tests

```bash
source venv/bin/activate
pytest                      # 132 tests, ~7s
pytest --cov=agentvault     # with coverage
```

---

## Blockchain: Base (Coinbase L2)

- **Fees**: < $0.001 per transaction
- **USDC**: native on Base (Circle)
- **Testnet**: Base Sepolia — free ETH and USDC via faucets
- **Faucets**:
  - ETH Sepolia: [faucet.quicknode.com](https://faucet.quicknode.com/base/sepolia)
  - USDC Sepolia: [faucet.circle.com](https://faucet.circle.com)

---

## Known limitations

- **Single-agent per wallet**: one private key = one wallet. Multi-agent setups require separate `.env` files.
- **SQLite WAL mode** handles concurrent reads safely, but `authorize() → commit()` is not atomic across separate processes on the same agent file. Fine for single-process agents.

---

## Self-hosting on a VPS

Runs on a Hetzner CX23 (€3.59/month) alongside other services. No additional infrastructure needed — one SQLite file per agent.

---

## Roadmap

- [x] Budget / whitelist / circuit breaker logic (`rules.py`)
- [x] Concurrent-safe SQLite persistence (`storage.py`)
- [x] Discord alerts (`notifier.py`)
- [x] `authorize() → commit()` orchestration (`wallet.py`)
- [x] Real USDC wallet on Base (`chain.py`)
- [x] CLI: status / history / reset / init
- [ ] Connect to a real agent (crypto-agent integration)
- [ ] GitHub Actions CI

---

## License

MIT — free to use, modify, and redistribute.
