# Contributing to AgentVault

Thank you for your interest in contributing to AgentVault! This document provides guidelines and instructions for contributing to this open-source budget controller for AI agents spending USDC on Base.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Architecture](#project-architecture)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Security Considerations](#security-considerations)
- [How to Contribute](#how-to-contribute)
- [Release Process](#release-process)

## Code of Conduct

This project is committed to providing a welcoming and inclusive experience for everyone. We expect all contributors to:

- Be respectful and constructive in all interactions
- Focus on what is best for the community and the project
- Show empathy towards others
- Accept constructive criticism gracefully

## Getting Started

### Prerequisites

- Python 3.11 or higher
- pip or pipenv for package management
- Git
- A Base wallet with ETH for gas (for integration testing)

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:

```bash
git clone https://github.com/YOUR_USERNAME/agentvault.git
cd agentvault
```

3. Add the upstream remote:

```bash
git remote add upstream https://github.com/BigFoot3/agentvault.git
```

## Development Setup

### 1. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode with all development dependencies (pytest, pytest-cov).

### 3. Verify Setup

```bash
# Run tests
pytest

# Check CLI works
agentvault --help
```

### 4. Environment Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Required environment variables:

| Variable | Description | Required For |
|----------|-------------|--------------|
| `AGENT_PRIVATE_KEY` | Wallet private key for signing transactions | Production |
| `AGENT_WALLET_ADDRESS` | Your wallet address on Base | Production |
| `DISCORD_WEBHOOK_URL` | Discord webhook for notifications | Optional |
| `CHAIN` | `base` or `base-sepolia` (default: base-sepolia) | Testing |
| `AGENTVAULT_DATA_DIR` | Directory for SQLite databases (default: ./data) | Always |

## Project Architecture

### Module Overview

```
agentvault/
├── __init__.py      # Public API exports
├── wallet.py        # AgentWallet — main orchestrator
├── rules.py         # BudgetRules, AuthResult — authorization logic
├── chain.py         # Chain — blockchain interactions (USDC transfers)
├── storage.py       # Storage — SQLite persistence
├── notifier.py      # Notifier — Discord webhook alerts
├── exceptions.py    # Custom exception classes
└── cli.py           # Command-line interface
```

### Core Flow

```
Agent → wallet.authorize() → rules.check() → storage.load()
                                    ↓
                              if approved:
                                  wallet.commit() → chain.transfer_usdc()
                                                  → storage.save()
                                                  → notifier.approved()
                              else:
                                  notifier.denied()
```

### Key Design Principles

1. **Statelessness**: Wallet is stateless between calls — state is read from disk each time for thread safety
2. **Mandatory Pattern**: `authorize()` must always be followed by `commit()` for approved transactions
3. **Fail-Safe**: Circuit breaker trips after N failures in X minutes
4. **Dry-Run Mode**: `onchain=False` allows testing without real transactions

## Coding Standards

### Python Style

- Follow PEP 8
- Use type hints for all function signatures
- Maximum line length: 100 characters
- Use docstrings for all public classes and methods (Google style)

### Example

```python
def compute_spent(transactions: list[dict], since: datetime) -> float:
    """
    Calculate total spent amount since a given datetime.

    Args:
        transactions: List of transaction dictionaries with 'amount' and 'timestamp' keys.
        since: The datetime to calculate spending from.

    Returns:
        Total spent amount as a float.
    """
    total = 0.0
    for tx in transactions:
        tx_time = datetime.fromisoformat(tx["timestamp"])
        if tx_time >= since:
            total += tx["amount"]
    return total
```

### Naming Conventions

- Classes: `PascalCase` (e.g., `AgentWallet`, `BudgetRules`)
- Functions/Variables: `snake_case` (e.g., `compute_spent`, `auth_result`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `_USDC_DECIMALS`, `_BUDGET_WARNING_PCT`)
- Private methods: `_leading_underscore` (e.g., `_data_dir`, `_send`)

### Import Order

1. Standard library imports
2. Third-party imports
3. Local module imports

```python
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from web3 import Web3

from .exceptions import BudgetExceeded
from .rules import AuthResult
```

## Testing Guidelines

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=agentvault --cov-report=html

# Run specific test file
pytest tests/test_rules.py

# Run with verbose output
pytest -v
```

### Test Coverage

- Aim for 80%+ code coverage
- All new features must include tests
- Critical paths (authorization, transfers) require comprehensive testing

### Writing Tests

```python
def test_budget_exceeded():
    """Test that authorization fails when budget is exceeded."""
    rules = BudgetRules(
        budget_usdc=100.0,
        period="week",
        max_per_tx=50.0,
        whitelist=[]
    )
    
    # Simulate spending 90 USDC
    state = {
        "transactions": [
            {"amount": 90.0, "timestamp": datetime.now(timezone.utc).isoformat()}
        ],
        "circuit_breaker": {"tripped": False, "failures": []}
    }
    
    result = rules.check(state, amount=20.0, to="0xABC...")
    
    assert not result.approved
    assert "budget" in result.reason.lower()
```

### Test Categories

1. **Unit Tests**: Test individual functions in isolation (`test_rules.py`, `test_storage.py`)
2. **Integration Tests**: Test component interactions (`test_wallet.py`)
3. **Chain Tests**: Mock blockchain interactions (`test_chain.py`)

## Security Considerations

### Critical Security Areas

1. **Private Key Handling**
   - Never commit private keys to the repository
   - Always use environment variables or secure key management
   - `.env` is in `.gitignore` — never remove it

2. **Transaction Authorization**
   - The `authorize() → commit()` pattern is mandatory
   - Never skip authorization checks
   - Always validate amounts and addresses

3. **Circuit Breaker**
   - Protects against runaway agents
   - Don't disable in production without careful consideration

4. **Whitelist Validation**
   - Empty whitelist allows all addresses (use with caution)
   - Always validate addresses are checksummed

### Security Testing

```bash
# Check for secrets in code
git-secrets --scan

# Run security linter
bandit -r agentvault/
```

## How to Contribute

### Reporting Bugs

1. Check if the bug is already reported in [Issues](https://github.com/BigFoot3/agentvault/issues)
2. Create a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Python version and environment details
   - Error messages and stack traces

### Suggesting Features

1. Open an issue with the `enhancement` label
2. Describe the use case and proposed solution
3. Discuss with maintainers before implementing

### Pull Request Process

1. **Create a Branch**
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. **Make Changes**
   - Write clean, documented code
   - Add tests for new functionality
   - Update README.md if needed

3. **Run Quality Checks**
   ```bash
   # Run tests
   pytest
   
   # Check code style (if using black)
   black agentvault/ tests/
   
   # Type checking (if using mypy)
   mypy agentvault/
   ```

4. **Commit Changes**
   - Use clear, descriptive commit messages
   - Reference issues when applicable
   
   ```
   feat: add support for monthly budget periods
   
   - Implement get_period_start() for "month" period
   - Add tests for monthly budget calculations
   - Update documentation
   
   Fixes #123
   ```

5. **Push and Create PR**
   ```bash
   git push origin feature/your-feature-name
   ```
   
   Then create a Pull Request on GitHub with:
   - Clear title and description
   - Link to related issues
   - Screenshots/logs if applicable
   - Checklist of changes

### Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation changes
- `test:` — Adding or updating tests
- `refactor:` — Code refactoring
- `perf:` — Performance improvements
- `chore:` — Maintenance tasks

## Release Process

1. Update version in `pyproject.toml`
2. Update `__version__` in `agentvault/__init__.py`
3. Update CHANGELOG.md
4. Create a git tag: `git tag v0.x.x`
5. Push tag: `git push origin v0.x.x`
6. GitHub Actions will build and publish to PyPI

## Questions?

- Open an issue for questions or discussions
- Join our community Discord (link in README)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to AgentVault! Together we're building safer AI agents on Base. 🤖💰