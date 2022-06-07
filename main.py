import os
from time import sleep
import requests
import radixlib as radix
from github import Github
from dotenv import load_dotenv
from typing import List
from tinydb import TinyDB, Query
import config

# Load environment variables
load_dotenv()

# Instantiate the wallet
xrd = radix.constants.XRD_RRI["stokenet"]
network: radix.network.Network = radix.network.STOKENET
wallet: radix.Wallet = radix.Wallet(
    provider=radix.Provider(network),
    signer=radix.Signer.from_mnemonic(os.getenv("MNEMONIC"))
)
print("Wallet address:", wallet.address)

# Connect to GitHub
g = Github(os.getenv("GITHUB_ACCESS_TOKEN"))
repo = g.get_repo("ScryptoPunks/database")

# Connect to the database
db = TinyDB("db.json")
Entry = Query()

pending = {}

# Look for transactions
txs = wallet.get_account_transactions(30)
for tx in txs[1]:
    if tx["message_blob"] is not None:
        msg: str = bytes.fromhex(tx["message_blob"]).decode(
            "utf-8").replace("\x00", "")
        if msg.startswith("trading"):
            stored_value = db.search(Entry.hash == tx["hash"])
            if not stored_value:
                actions: List[radix.actions.TransferTokens] = list(filter(
                    lambda x: isinstance(
                        x, radix.actions.TransferTokens) and x.from_account.address != wallet.address and x.to_account.address == wallet.address,  # type: ignore
                    tx['actions']
                ))
                amount = int(actions[0].amount / 10 ** 18)
                sender = actions[0].from_account.address
                token = actions[0].token_rri

                if token == config.TOKEN_RRI:
                    nonces = msg.split("trading")[1].split("for")[
                        0].split(", ")
                elif token == xrd:
                    nonces = msg.split("for")[1].split(", ")

                # Check if there's a match
                key = " ".join(str(nonce) for nonce in nonces)
                if key in pending:
                    # Configure transactions
                    royalties = max(10, amount * config.TRADING_FEE)
                    if pending[key]["isBuyer"]:
                        buyer = pending[key]["sender"]
                        seller = sender
                    else:
                        buyer = sender
                        seller = pending[key]["sender"]

                    tx_action_builder_buyer: radix.ActionBuilder = wallet.action_builder
                    tx_action_builder_buyer = tx_action_builder_buyer.token_transfer(
                        from_account_address=wallet.address,
                        to_account_address=buyer,
                        token_rri=config.TOKEN_RRI,
                        transfer_amount=len(nonces) * 10 ** 18
                    )
                    tx_action_builder_seller: radix.ActionBuilder = wallet.action_builder
                    tx_action_builder_seller = tx_action_builder_seller.token_transfer(
                        from_account_address=wallet.address,
                        to_account_address=seller,
                        token_rri=xrd,
                        transfer_amount=(amount - royalties) * 10 ** 18
                    )

                    # Build, sign and send transactions
                    wallet.build_sign_and_send_transaction(
                        actions=tx_action_builder_buyer,
                        message_string=f"traded {amount} XRD for {''.join(nonces)}"
                    )
                    wallet.build_sign_and_send_transaction(
                        actions=tx_action_builder_seller,
                        message_string=f"traded {''.join(nonces)} for {amount} XRD"
                    )

                    # Store the new entries in database
                    db.insert({"hash": tx["hash"]})
                    db.insert({"hash": tx["hash"]})
                    
                else:
                    pending[key] = {
                        "hash": tx["hash"], 
                        "amount": amount, 
                        "sender": sender, 
                        "isBuyer": token == xrd
                    }
