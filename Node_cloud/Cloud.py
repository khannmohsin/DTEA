import os
import subprocess
import json
from web3 import Web3
from cryptography.fernet import Fernet
from eth_account import Account

class Cloud:
    def __init__(self, cloud_id, web3_provider="http://127.0.0.1:8545"):
        self.cloud_id = cloud_id
        self.web3 = Web3(Web3.HTTPProvider(web3_provider))
        self.account = Account.create()  # Generate Ethereum Account
        self.data_path = "data"
        self.output_file = "cloud_publicKey.pub"
        self.genesis_file = "genesis.json"
        self.ibft_config = "cloud_ibftConfig.json"
        self.public_key = None
        self.prefunded_address = self.account.address  # Prefunded account

        # Save account information
        self.save_account()

    def save_account(self):
        """Save the prefunded account details."""
        accounts_path = os.path.join(self.data_path, "accounts.json")
        os.makedirs(self.data_path, exist_ok=True)

        account_data = {
            "address": self.prefunded_address,
            "private_key": self.account._private_key.hex()  # ✅ Corrected attribute
        }

        with open(accounts_path, "w") as f:
            json.dump(account_data, f, indent=4)

        print(f"✅ Prefunded account saved: {self.prefunded_address}")

    def generate_cloud_node_keys(self):
        """Generate or retrieve the Cloud Node's public & private keys."""
        public_keys_path = os.path.join(self.data_path, "public_keys")
        public_key_file = os.path.join(public_keys_path, self.output_file)

        os.makedirs(public_keys_path, exist_ok=True)

        if os.path.exists(public_key_file):
            print(f"🔑 Cloud Node public key already exists. Retrieving...")
        else:
            print("🔄 Generating new key pair for Cloud Node...")
            subprocess.run(["besu", "public-key", "export", "--to=" + public_key_file], check=True)

        with open(public_key_file, "r") as f:
            self.public_key = f.read().strip()

        print(f"✅ Cloud Node {self.cloud_id} Public Key: {self.public_key}")
        return self.public_key

    def generate_ibft_config(self):
        """Generate ibftConfig.json file automatically with correct node structure."""
        if not self.public_key:
            raise ValueError("❌ Public key is not generated yet. Run `generate_cloud_node_keys()` first.")

        ibft_config_data = {
            "genesis": {
                "config": {
                    "chainId": 1337,
                    "ibft2": {
                        "blockperiodseconds": 5,
                        "epochlength": 30000,
                        "requesttimeoutseconds": 10
                    }
                },
                "nonce": "0x0",
                "difficulty": "0x1",
                "gasLimit": "0x47b760"
            },
            "blockchain": {
                "nodes": {
                    self.public_key: {}
                }
            }
        }

        config_files_path = os.path.join(self.data_path, "config_files")
        os.makedirs(config_files_path, exist_ok=True)
        ibft_config_file = os.path.join(config_files_path, self.ibft_config)

        with open(ibft_config_file, "w") as f:
            json.dump(ibft_config_data, f, indent=4)

        print(f"✅ IBFT Configuration file `{ibft_config_file}` created.")

    def generate_genesis_file(self):
        """Generate `genesis.json` with pre-funded account (`alloc`)."""
        print("🔄 Generating `extraData` using Besu...")

        config_files_path = os.path.join(self.data_path, "config_files")
        ibft_config_file = os.path.join(config_files_path, self.ibft_config)

        genesis_files_path = os.path.join(self.data_path, "genesis_files")
        os.makedirs(genesis_files_path, exist_ok=True)

        # Run Besu command to generate blockchain config
        subprocess.run([
            "besu", "operator", "generate-blockchain-config",
            "--config-file=" + ibft_config_file,
            "--to=" + genesis_files_path
        ], check=False)

        # Load the generated `genesis.json` file
        generated_genesis_file = os.path.join(genesis_files_path, self.genesis_file)
        with open(generated_genesis_file, "r") as f:
            genesis_data = json.load(f)

        # Extract `extraData` and update `genesis.json`
        extra_data = genesis_data["extraData"]

        genesis_data["config"]["ibft2"]["validators"] = [self.public_key]
        genesis_data["extraData"] = extra_data

        # ✅ Include `alloc` with a prefunded account
        genesis_data["alloc"] = {
            self.prefunded_address: {
                "balance": "1000000000000000000000000"  # Prefund with 1M ETH (adjust as needed)
            }
        }

        # Save updated `genesis.json`
        with open(generated_genesis_file, "w") as f:
            json.dump(genesis_data, f, indent=4)

        print(f"✅ `genesis.json` successfully generated with pre-funded account.")

    def initialize_blockchain(self):
        """Initialize Besu blockchain with the generated `genesis.json`."""
        print("🔄 Initializing blockchain with `genesis.json`...")

        genesis_files_path = os.path.join(self.data_path, "genesis_files")
        generated_genesis_file = os.path.join(genesis_files_path, self.genesis_file)

        subprocess.run(["besu", "--data-path=" + self.data_path, "--genesis-file=" + generated_genesis_file], check=True)
        print("✅ Blockchain initialized successfully.")

    def start_cloud_node(self):
        """Start the Cloud Node as a validator in the network."""
        genesis_files_path = os.path.join(self.data_path, "genesis_files")
        generated_genesis_file = os.path.join(genesis_files_path, self.genesis_file)

        print("🚀 Starting Cloud Node...")
        subprocess.run([
            "besu",
            "--data-path=" + self.data_path,
            "--genesis-file=" + generated_genesis_file,
            "--rpc-http-enabled",
            "--rpc-http-api=ETH,NET,IBFT",
            "--p2p-port=30303",
            "--rpc-http-port=8545"
        ], check=True)
        print("✅ Cloud Node is up and running!")

# 🚀 Automate the Cloud Node Setup
cloud = Cloud(cloud_id=1)
cloud.generate_cloud_node_keys()
cloud.generate_ibft_config()
cloud.generate_genesis_file()
cloud.start_cloud_node()