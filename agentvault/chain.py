"""
AgentVault — couche blockchain.

Gère les transferts USDC réels sur Base (mainnet) et Base Sepolia (tests).
Branché dans wallet.py à l'intérieur de commit().

Contrats USDC :
  Base mainnet : 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
  Base Sepolia  : 0x036CbD53842c5426634e7929541eC2318f3dCF7e

Usage :
  chain = Chain()                      # lit AGENT_PRIVATE_KEY + CHAIN dans .env
  tx_hash = chain.transfer_usdc(to="0xABC...", amount_usdc=5.0)
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from decimal import Decimal

load_dotenv()

# ---------------------------------------------------------------------------
# Constantes réseau
# ---------------------------------------------------------------------------

_NETWORKS = {
    "base": {
        "rpc":          "https://mainnet.base.org",
        "chain_id":     8453,
        "usdc":         "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "explorer":     "https://basescan.org/tx/",
    },
    "base-sepolia": {
        "rpc":          "https://sepolia.base.org",
        "chain_id":     84532,
        "usdc":         "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "explorer":     "https://sepolia.basescan.org/tx/",
    },
}

# ABI minimal ERC-20 : transfer + balanceOf
_ERC20_ABI = [
    {
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount",    "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# USDC = 6 décimales
_USDC_DECIMALS = 6


# ---------------------------------------------------------------------------
# Dataclass résultat
# ---------------------------------------------------------------------------

@dataclass
class TxReceipt:
    tx_hash:      str
    explorer_url: str
    gas_used:     int
    status:       int   # 1 = succès, 0 = échec


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------

class Chain:
    """
    Interface blockchain pour AgentVault.

    Lit depuis .env :
      AGENT_PRIVATE_KEY : clé privée 0x-prefixed (obligatoire)
      CHAIN             : "base" | "base-sepolia" (défaut : "base-sepolia")
      INFURA_API_KEY    : optionnel, utilisé si le RPC public échoue
    """

    def __init__(self) -> None:
        private_key = os.getenv("AGENT_PRIVATE_KEY", "")
        if not private_key:
            raise ValueError("AGENT_PRIVATE_KEY manquant dans .env")

        chain_name = os.getenv("CHAIN", "base-sepolia")
        if chain_name not in _NETWORKS:
            raise ValueError(
                f"CHAIN invalide : '{chain_name}'. Valeurs acceptées : {list(_NETWORKS)}"
            )

        net = _NETWORKS[chain_name]
        self.chain_name   = chain_name
        self.chain_id     = net["chain_id"]
        self.explorer_url = net["explorer"]
        self._usdc_addr   = Web3.to_checksum_address(net["usdc"])

        # Connexion RPC — public d'abord, Infura en fallback
        self._w3 = self._connect(net["rpc"], chain_name)

        # Compte agent
        self._account = self._w3.eth.account.from_key(private_key)
        self.address  = self._account.address

        # Contrat USDC
        self._usdc = self._w3.eth.contract(
            address=self._usdc_addr,
            abi=_ERC20_ABI,
        )

        print(
            f"[Chain] Connecté à {chain_name} | "
            f"agent={self.address} | "
            f"ETH={self._eth_balance():.6f} | "
            f"USDC={self.usdc_balance():.2f}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def transfer_usdc(self, to: str, amount_usdc: float) -> TxReceipt:
        """
        Envoie `amount_usdc` USDC vers `to` sur Base.

        Args:
            to:          Adresse destinataire (checksum ou minuscule).
            amount_usdc: Montant en USDC (ex : 5.0 = 5 USDC).

        Returns:
            TxReceipt avec tx_hash, url explorateur, gas utilisé et status.

        Raises:
            ValueError:   Adresse invalide ou montant <= 0.
            RuntimeError: Solde insuffisant ou tx échouée on-chain.
        """
        if amount_usdc <= 0:
            raise ValueError(f"Montant invalide : {amount_usdc}")

        to_addr = Web3.to_checksum_address(to)
        amount_raw = int(Decimal(str(amount_usdc)) * 10 ** _USDC_DECIMALS)

        # Vérification solde USDC
        balance_usdc = self.usdc_balance()
        if amount_usdc > balance_usdc:
            raise RuntimeError(
                f"Solde USDC insuffisant : {balance_usdc:.2f} disponibles, "
                f"{amount_usdc:.2f} demandés"
            )

        # Vérification solde ETH (gas)
        eth_balance = self._eth_balance()
        if eth_balance < 0.0001:
            raise RuntimeError(
                f"Solde ETH insuffisant pour le gas : {eth_balance:.6f} ETH"
            )

        # Construction de la transaction
        nonce = self._w3.eth.get_transaction_count(self.address)

        tx = self._usdc.functions.transfer(to_addr, amount_raw).build_transaction({
            "chainId":  self.chain_id,
            "from":     self.address,
            "nonce":    nonce,
        })

        # Estimation du gas + marge 20%
        estimated_gas = self._w3.eth.estimate_gas(tx)
        tx["gas"] = int(estimated_gas * 1.2)

        # Signature + envoi
        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)

        print(
            f"[Chain] TX envoyée : {self.explorer_url}{tx_hash.hex()} | "
            f"{amount_usdc} USDC → {to_addr}",
            flush=True,
        )

        # Attente de la confirmation (Base ~2s)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt.status != 1:
            raise RuntimeError(
                f"Transaction échouée on-chain : {self.explorer_url}{tx_hash.hex()}"
            )

        print(
            f"[Chain] TX confirmée | gas={receipt.gasUsed} | "
            f"{self.explorer_url}{tx_hash.hex()}",
            flush=True,
        )

        return TxReceipt(
            tx_hash=tx_hash.hex(),
            explorer_url=f"{self.explorer_url}{tx_hash.hex()}",
            gas_used=receipt.gasUsed,
            status=receipt.status,
        )

    def usdc_balance(self) -> float:
        """Retourne le solde USDC de l'agent en unités lisibles."""
        raw = self._usdc.functions.balanceOf(self.address).call()
        return raw / 10 ** _USDC_DECIMALS

    def eth_balance(self) -> float:
        """Retourne le solde ETH de l'agent."""
        return self._eth_balance()

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _connect(self, rpc_url: str, chain_name: str) -> Web3:
        """Tente de se connecter au RPC public, puis Infura en fallback."""
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Middleware POA nécessaire pour Base
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if w3.is_connected():
            return w3

        # Fallback Infura
        infura_key = os.getenv("INFURA_API_KEY", "")
        if infura_key:
            infura_rpc = {
                "base":         f"https://base-mainnet.infura.io/v3/{infura_key}",
                "base-sepolia": f"https://base-sepolia.infura.io/v3/{infura_key}",
            }.get(chain_name, rpc_url)

            w3_infura = Web3(Web3.HTTPProvider(infura_rpc))
            w3_infura.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            if w3_infura.is_connected():
                print(f"[Chain] RPC public indisponible — fallback Infura", flush=True)
                return w3_infura

        raise RuntimeError(
            f"Impossible de se connecter au réseau {chain_name}. "
            "Vérifiez INFURA_API_KEY dans .env"
        )

    def _eth_balance(self) -> float:
        raw = self._w3.eth.get_balance(self.address)
        return float(self._w3.from_wei(raw, "ether"))
