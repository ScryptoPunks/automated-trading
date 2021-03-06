import os
import json
from datetime import datetime
from time import sleep
import math
import radixlib as radix
from github import Github
from dotenv import load_dotenv
from typing import List
from tinydb import TinyDB, Query
import discord
import config

# Load environment variables
load_dotenv()

# Instantiate the wallet
xrd = radix.constants.XRD_RRI["mainnet"]
network: radix.network.Network = radix.network.MAINNET
wallet: radix.Wallet = radix.Wallet(
    provider=radix.Provider(network),
    signer=radix.Signer.from_mnemonic(os.getenv("MNEMONIC"))
)
print("Wallet address:", wallet.address)

# Connect to Github
g = Github(os.getenv("GITHUB_ACCESS_TOKEN"))
repo = g.get_repo("ScryptoPunks/database")
content = repo.get_contents("database.json")
decoded = content.decoded_content.decode()
database = json.loads(decoded)

# Connect to the database
db = TinyDB("db.json")
Entry = Query()

# Connect to Discord
client = discord.Client()


@client.event
async def on_ready():
    print("Bot active.")

    # Look for transactions
    pending = {}
    txs = wallet.get_account_transactions(30)
    for tx in txs[1]:
        if tx["message_blob"] is not None:
            msg: str = bytes.fromhex(tx["message_blob"]).decode(
                "utf-8").replace("\x00", "").lower()
            if msg.startswith("trading"):
                stored_value = db.search(Entry.hash == tx["hash"])
                if not stored_value:  # Trade not processed yet
                    actions: List[radix.actions.TransferTokens] = list(filter(
                        lambda x: isinstance(
                            x, radix.actions.TransferTokens) and x.from_account.address != wallet.address and x.to_account.address == wallet.address,
                        tx['actions']
                    ))
                    amount = int(actions[0].amount / 10 ** 18)
                    sender = actions[0].from_account.address
                    token = actions[0].token_rri

                    if token == config.TOKEN_RRI:
                        nonces = msg.split("trading ")[1].split(" for")[
                            0].split(", ")
                        if amount != len(nonces):
                            continue
                        valid = True
                        for nonce in nonces:
                            if database[nonce.strip()] != sender:
                                valid = False
                                break
                        if not valid:
                            continue
                    elif token == xrd:
                        nonces = msg.split("for ")[1].split(", ")
                        if amount != int(msg.split("xrd")[0].split("trading")[
                                1].strip()):
                            continue

                    key = " ".join(str(nonce) for nonce in nonces)
                    if key in pending:  # Found match
                        if pending[key]["isBuyer"]:
                            buyer = pending[key]["sender"]
                            seller = sender
                            msg_xrd_amount = pending[key]["amount"]
                            xrd_amount = (
                                pending[key]["amount"] - max(10, pending[key]["amount"] * config.TRADING_FEE)) * 10 ** 18
                        else:
                            buyer = sender
                            seller = pending[key]["sender"]
                            msg_xrd_amount = amount
                            xrd_amount = (amount - max(10, amount *
                                                       config.TRADING_FEE)) * 10 ** 18

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
                            transfer_amount=xrd_amount
                        )

                        # Build, sign and send transactions
                        wallet.build_sign_and_send_transaction(
                            actions=tx_action_builder_buyer,
                            message_string=f"traded {msg_xrd_amount} XRD for {' '.join(nonces)}"
                        )
                        sleep(20)
                        wallet.build_sign_and_send_transaction(
                            actions=tx_action_builder_seller,
                            message_string=f"traded {' '.join(nonces)} for {msg_xrd_amount} XRD"
                        )
                        sleep(20)

                        # Store the new entries in database
                        db.insert({"hash": pending[key]["hash"]})
                        db.insert({"hash": tx["hash"]})

                        # Update Github repo
                        for nonce in nonces:
                            database[nonce] = buyer
                        repo.update_file(
                            content.path,
                            f"{int(xrd_amount / 10 ** 18 / 0.9)} XRD for #{', '.join(str(nonce) for nonce in nonces)}",
                            json.dumps(database).encode("utf-8"),
                            content.sha
                        )

                        # Update Discord channels & post sale
                        for nonce in nonces:
                            emb = discord.Embed()
                            emb.title = f"{config.NAME} #{nonce} was just sold!"
                            emb.color = 0x2ECC40
                            emb.set_thumbnail(
                                url=f"https://scryptopunks.com/images/{nonce}.png")
                            emb.add_field(name="Transaction",
                                          value=f"[{tx['hash'][:4]}...{tx['hash'][-4:]}](https://explorer.radixdlt.com/#/transactions/{tx['hash']})", inline=True)
                            emb.add_field(
                                name="Price", value=f"{round(msg_xrd_amount, 2)} XRD", inline=True)
                            emb.timestamp = datetime.fromtimestamp(math.floor(
                                tx["confirmed_time"].timestamp()))
                            await client.get_channel(config.SALE_CHANNEL).send(embed=emb)

                        volume = int(client.get_channel(config.VOLUME_CHANNEL).name.split(
                            "Volume: ")[1].split(" XRD")[0].replace(",", ""))
                        await client.get_channel(config.VOLUME_CHANNEL).edit(name=f"Volume: {volume + msg_xrd_amount} XRD")

                    else:
                        pending[key] = {
                            "hash": tx["hash"],
                            "amount": amount,
                            "sender": sender,
                            "isBuyer": token == xrd
                        }
    
    print("Bot inactive.")
    await client.close()

client.run(os.getenv("DISCORD_BOT_TOKEN"))
