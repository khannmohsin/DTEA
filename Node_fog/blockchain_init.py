import json
from eth_account import Account
import subprocess
import os
from eth_keys import keys

class BlockchainInit:
    def __init__(self):
        self.public_private_key_generation = None
        self.genesis_file_creation = None
        self.qbft_file_generation = None

    def generate_keys(self):
        """Generates a new Ethereum account (private key and address)."""
        account = Account.create()
        os.makedirs("/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_fog/data", exist_ok=True)

        with open("/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_fog/data/key.priv", "w") as priv_file:
            private_key_bytes = os.urandom(32)
            priv_key = keys.PrivateKey(private_key_bytes)
            priv_file.write(priv_key.to_hex())
            
            # priv_file.write(private_key)

        with open("/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_fog/data/key.pub", "w") as pub_file:
            public_key = priv_key.public_key
            pub_file.write(public_key.to_hex())
            # pub_file.write(public_key)


    def create_genesis_file(self, qbft_config_path):

        genesis_files_path = "/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_fog/data"

        # Run Besu command to generate blockchain config
        subprocess.run([
            "besu", "operator", "generate-blockchain-config",
            "--config-file=" + qbft_config_path,
            "--to=" + genesis_files_path
        ], check=False)

        print(f"✅ `genesis.json` successfully generated with pre-funded account.")

    # Function to generate a new Ethereum account
    def generate_account(self):
        """Generates a new Ethereum account (private key and address)."""
        account = Account.create()
        return {
            "private_key": account._private_key.hex(),  # Store as hex
            "address": account.address
        }

    def create_qbft_file(self, num_prefunded_accounts, num_validators):
        PREFUNDED_ACCOUNT_FILE = "/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/prefunded_keys.json"
        CONFIG_FILE = "/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/qbftConfigFile.json"
        CHAIN_ID = 1337
        BLOCK_PERIOD_SECONDS = 2
        EPOCH_LENGTH = 30000
        REQUEST_TIMEOUT_SECONDS = 4
        GAS_LIMIT = "0x47b760"
        DIFFICULTY = "0x1"

        prefunded_accounts = [self.generate_account() for _ in range(num_prefunded_accounts)]

        # Create the QBFT configuration
        qbft_config = {
            "genesis": {
                "config": {
                    "chainId": CHAIN_ID,
                    "berlinBlock": 0,
                    "qbft": {
                        "blockperiodseconds": BLOCK_PERIOD_SECONDS,
                        "epochlength": EPOCH_LENGTH,
                        "requesttimeoutseconds": REQUEST_TIMEOUT_SECONDS
                    }
                },
                "nonce": "0x0",
                "timestamp": "0x58ee40ba",
                "gasLimit": GAS_LIMIT,
                "difficulty": DIFFICULTY,
                # "mixHash": "0x63746963616c2062797a616e74696e6365206674756c7420746f6c6572616e6365",
                "coinbase": "0x0000000000000000000000000000000000000000",
                "alloc": {acct["address"]: {"balance": "90000000000000000000000"} for acct in prefunded_accounts}
            },
            "blockchain": {
                "nodes": {
                    "generate": False,  # We generated validators manually
                    "count": num_validators
                }
            }
        }

        # Save the configuration file
        with open(CONFIG_FILE, "w") as f:
            json.dump(qbft_config, f, indent=4)

        # Save generated keys for later use
        with open(PREFUNDED_ACCOUNT_FILE, "w") as f:
            json.dump({
                "prefunded_accounts": prefunded_accounts
            }, f, indent=4)

        print(f"QBFT Configuration file `{CONFIG_FILE}` created successfully!")
        print(f"Prefunded account keys saved in `{PREFUNDED_ACCOUNT_FILE}`")


    def start_blockchain_node(self):
        
        besu_command = [
            "besu",
            "--data-path=data",
            "--node-private-key-file=data/key.priv",
            "--genesis-file=data/genesis.json",
            "--rpc-http-enabled",
            "--rpc-http-api=ETH,NET,QBFT",
            "--host-allowlist=*",
            "--rpc-http-cors-origins=all"
        ]

        # Run Besu as a subprocess
        try:
            print("🚀 Starting Besu node...")
            process = subprocess.Popen(besu_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Print output in real-time
            for line in process.stdout:
                print(line, end="")

            for line in process.stderr:
                print(line, end="")

            # Wait for the process to complete
            process.wait()
        except FileNotFoundError:
            print("❌ Error: Besu is not installed or not found in PATH.")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")   
    