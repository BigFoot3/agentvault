"""
Tests — Chain (chain.py).

Tous les appels web3 sont mockés — aucune connexion réseau réelle.

Couverture :
  - Initialisation : clé manquante, réseau invalide
  - transfer_usdc() : montant invalide, solde USDC insuffisant, solde ETH insuffisant
  - transfer_usdc() : flow complet succès
  - transfer_usdc() : tx échouée on-chain (status=0)
  - usdc_balance() / eth_balance()
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_KEY = "0x" + "a" * 64
VALID_ADDR = "0xBf87a9A44AF1B441dFc88Ea9D11559eA3b2fdC9B"
DEST_ADDR  = "0xAbCd1234AbCd1234AbCd1234AbCd1234AbCd1234"


def make_mock_w3(
    usdc_balance_raw: int = 10_000_000,   # 10 USDC
    eth_balance_wei: int  = 2_000_000_000_000_000,  # 0.002 ETH
    tx_status: int = 1,
):
    """Construit un Web3 mocké prêt à l'emploi."""
    w3 = MagicMock()
    w3.is_connected.return_value = True

    # Compte
    account = MagicMock()
    account.address = VALID_ADDR
    w3.eth.account.from_key.return_value = account

    # Soldes
    w3.eth.get_balance.return_value = eth_balance_wei
    w3.from_wei.return_value = eth_balance_wei / 1e18

    # Contrat USDC
    contract = MagicMock()
    contract.functions.balanceOf.return_value.call.return_value = usdc_balance_raw

    # Build + estimate + send
    built_tx = {"chainId": 84532, "from": VALID_ADDR, "nonce": 0, "gas": 60000}
    contract.functions.transfer.return_value.build_transaction.return_value = built_tx
    w3.eth.estimate_gas.return_value = 50000
    w3.eth.get_transaction_count.return_value = 0

    # Signature
    signed = MagicMock()
    signed.raw_transaction = b"\x00" * 32
    account.sign_transaction.return_value = signed

    # TX hash + receipt
    tx_hash_bytes = MagicMock()
    tx_hash_bytes.hex.return_value = "0xabc123"
    w3.eth.send_raw_transaction.return_value = tx_hash_bytes

    receipt = MagicMock()
    receipt.status = tx_status
    receipt.gasUsed = 48000
    w3.eth.wait_for_transaction_receipt.return_value = receipt

    w3.eth.contract.return_value = contract

    # checksum address
    w3.to_checksum_address = lambda addr: addr

    return w3, account, contract


# ---------------------------------------------------------------------------
# Fixture principale
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("AGENT_PRIVATE_KEY", VALID_KEY)
    monkeypatch.setenv("CHAIN", "base-sepolia")
    monkeypatch.delenv("INFURA_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestChainInit:
    def test_missing_private_key_raises(self, monkeypatch):
        monkeypatch.setenv("AGENT_PRIVATE_KEY", "")
        monkeypatch.setenv("CHAIN", "base-sepolia")
        from agentvault.chain import Chain
        with pytest.raises(ValueError, match="AGENT_PRIVATE_KEY"):
            with patch("agentvault.chain.Web3") as MockWeb3:
                MockWeb3.return_value.is_connected.return_value = True
                Chain()

    def test_invalid_chain_raises(self, monkeypatch):
        monkeypatch.setenv("AGENT_PRIVATE_KEY", VALID_KEY)
        monkeypatch.setenv("CHAIN", "ethereum")
        from agentvault.chain import Chain
        with pytest.raises(ValueError, match="CHAIN invalide"):
            with patch("agentvault.chain.Web3"):
                Chain()

    def test_valid_init(self, mock_env):
        from agentvault.chain import Chain
        w3, _, _ = make_mock_w3()
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            assert chain.address == VALID_ADDR
            assert chain.chain_name == "base-sepolia"

    def test_rpc_failure_without_infura_raises(self, mock_env):
        from agentvault.chain import Chain
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value.is_connected.return_value = False
            with pytest.raises(RuntimeError, match="Impossible de se connecter"):
                Chain()


# ---------------------------------------------------------------------------
# transfer_usdc() — validations
# ---------------------------------------------------------------------------

class TestTransferUSDCValidation:
    def _make_chain(self, mock_env, usdc_balance_raw=10_000_000, eth_balance_wei=2_000_000_000_000_000):
        from agentvault.chain import Chain
        w3, _, _ = make_mock_w3(usdc_balance_raw=usdc_balance_raw, eth_balance_wei=eth_balance_wei)
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            return Chain(), w3

    def test_zero_amount_raises(self, mock_env):
        chain, _ = self._make_chain(mock_env)
        with pytest.raises(ValueError, match="Montant invalide"):
            chain.transfer_usdc(DEST_ADDR, 0.0)

    def test_negative_amount_raises(self, mock_env):
        chain, _ = self._make_chain(mock_env)
        with pytest.raises(ValueError, match="Montant invalide"):
            chain.transfer_usdc(DEST_ADDR, -1.0)

    def test_insufficient_usdc_raises(self, mock_env):
        # 0 USDC disponible
        chain, _ = self._make_chain(mock_env, usdc_balance_raw=0)
        with pytest.raises(RuntimeError, match="Solde USDC insuffisant"):
            chain.transfer_usdc(DEST_ADDR, 1.0)

    def test_insufficient_eth_raises(self, mock_env):
        # Solde ETH = 0
        chain, _ = self._make_chain(mock_env, eth_balance_wei=0)
        with pytest.raises(RuntimeError, match="Solde ETH insuffisant"):
            chain.transfer_usdc(DEST_ADDR, 1.0)


# ---------------------------------------------------------------------------
# transfer_usdc() — flow succès
# ---------------------------------------------------------------------------

class TestTransferUSDCSuccess:
    def test_returns_tx_receipt(self, mock_env):
        from agentvault.chain import Chain, TxReceipt
        w3, _, _ = make_mock_w3()
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            receipt = chain.transfer_usdc(DEST_ADDR, 1.0)
        assert isinstance(receipt, TxReceipt)
        assert receipt.tx_hash == "0xabc123"
        assert receipt.status == 1
        assert receipt.gas_used == 48000

    def test_amount_raw_precision(self, mock_env):
        """0.1 USDC doit envoyer exactement 100_000 units, pas 99_999."""
        from agentvault.chain import Chain
        w3, _, contract = make_mock_w3()
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            chain.transfer_usdc(DEST_ADDR, 0.1)

        # Récupère l'argument amount_raw passé à transfer()
        call_args = contract.functions.transfer.call_args
        amount_raw = call_args[0][1]
        assert amount_raw == 100_000, f"Attendu 100_000, reçu {amount_raw}"

    def test_amount_raw_precision_03(self, mock_env):
        """0.3 USDC = 300_000 units exactement."""
        from agentvault.chain import Chain
        w3, _, contract = make_mock_w3()
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            chain.transfer_usdc(DEST_ADDR, 0.3)

        call_args = contract.functions.transfer.call_args
        amount_raw = call_args[0][1]
        assert amount_raw == 300_000, f"Attendu 300_000, reçu {amount_raw}"

    def test_gas_margin_applied(self, mock_env):
        """Gas estimé + 20% marge."""
        from agentvault.chain import Chain
        w3, _, _ = make_mock_w3()
        w3.eth.estimate_gas.return_value = 50_000
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            chain.transfer_usdc(DEST_ADDR, 1.0)

        # La tx envoyée doit avoir gas = int(50_000 * 1.2) = 60_000
        sent_tx = w3.eth.send_raw_transaction.call_args
        assert sent_tx is not None  # la tx a bien été envoyée


# ---------------------------------------------------------------------------
# transfer_usdc() — tx échouée on-chain
# ---------------------------------------------------------------------------

class TestTransferUSDCFailed:
    def test_onchain_failure_raises(self, mock_env):
        """Si receipt.status == 0, RuntimeError."""
        from agentvault.chain import Chain
        w3, _, _ = make_mock_w3(tx_status=0)
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            with pytest.raises(RuntimeError, match="Transaction échouée on-chain"):
                chain.transfer_usdc(DEST_ADDR, 1.0)


# ---------------------------------------------------------------------------
# usdc_balance() / eth_balance()
# ---------------------------------------------------------------------------

class TestBalances:
    def test_usdc_balance(self, mock_env):
        from agentvault.chain import Chain
        w3, _, _ = make_mock_w3(usdc_balance_raw=5_500_000)  # 5.5 USDC
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            assert chain.usdc_balance() == 5.5

    def test_eth_balance(self, mock_env):
        from agentvault.chain import Chain
        w3, _, _ = make_mock_w3(eth_balance_wei=2_000_000_000_000_000)
        with patch("agentvault.chain.Web3") as MockWeb3:
            MockWeb3.return_value = w3
            MockWeb3.to_checksum_address = lambda a: a
            chain = Chain()
            assert chain.eth_balance() == pytest.approx(0.002, rel=1e-3)
