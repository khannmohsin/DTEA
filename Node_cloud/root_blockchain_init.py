import json
from eth_account import Account
import subprocess
import os
import sys
from eth_keys import keys

class BlockchainInit:
    def __init__(self):
        self.root_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/"
        self.prefunded_account_file = os.path.join(self.root_path, "prefunded_keys.json")
        self.config_file = os.path.join(self.root_path, "qbftConfigFile.json")
        self.data_path = os.path.join(self.root_path, "data/")
        self.genesis_files_path = os.path.join(self.root_path, "genesis/")
        self.private_key = os.path.join(self.data_path, "key.priv")
        self.public_key = os.path.join(self.data_path, "key.pub")
        self.validator_addresses = os.path.join(self.genesis_files_path, "validator_address.json")
        self.genesis_file = os.path.join(self.genesis_files_path, "genesis.json")

        
    
    #---------------------Node Public and Private generation----------------------------
    def generate_keys(self):
        """Generates a new Ethereum account (private key and address)."""
        account = Account.create()
        os.makedirs(self.data_path, exist_ok=True)

        with open(self.private_key, "w") as priv_file:
            private_key_bytes = os.urandom(32)
            priv_key = keys.PrivateKey(private_key_bytes)
            priv_file.write(priv_key.to_hex())

        with open(self.public_key, "w") as pub_file:
            public_key = priv_key.public_key
            pub_file.write(public_key.to_hex())

    #---------------------Create etherium accounts with ETH balance----------------------------
    def generate_account(self):
        """Generates a new Ethereum account (private key and address)."""
        account = Account.create()
        return {
            "private_key": account._private_key.hex(),  # Store as hex
            "address": account.address
        }
    

    #---------------------Update Validators----------------------------

    def start_validator_event_listener():
        try:
            process = subprocess.Popen(
                ["node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "listenForValidatorProposals"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            print("Validator proposal event listener started (running in background)...")
            return process  # You can keep a reference to manage the process (e.g., stop later)
        except Exception as e:
            print(" Failed to start event listener:", e)
            return None  

    #---------------------Create QBFT config file----------------------------
    def create_qbft_file(self, num_prefunded_accounts, num_validators):
        num_prefunded_accounts = int(num_prefunded_accounts)  # Ensure it's an integer
        num_validators = int(num_validators) 
        
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
        with open(self.config_file, "w") as f:
            json.dump(qbft_config, f, indent=4)

        # Save generated keys for later use
        with open(self.prefunded_account_file, "w") as f:
            json.dump({
                "prefunded_accounts": prefunded_accounts
            }, f, indent=4)

        print(f"\n QBFT Configuration file generated successfully at location: `{self.config_file}` \n")
        print(f"\n Prefunded account keys saved in:  `{self.prefunded_account_file}`\n")

    #---------------------Create genesis file from the QBFT config file----------------------------
    def create_genesis_file(self, qbft_config_path):

        # Run Besu command to generate blockchain config
        subprocess.run([
            "besu", "operator", "generate-blockchain-config",
            "--config-file=" + qbft_config_path,
            "--to=" + self.genesis_files_path
        ], check=False)

        print(f"\nGenesis file generated successfully at loc: {self.genesis_files_path}\n")

    #---------------------Update the genesis file with updated extradata----------------------------
    def update_genesis_file(self):
        """Extract Ethereum address from private key using Besu CLI and store in JSON."""
        
        # Run Besu command to extract Ethereum address from private key
        result = subprocess.run(
            ["besu", "public-key", "export-address", "--node-private-key-file=" + self.private_key],
            capture_output=True, text=True, check=False
        )

        # If the command was successful, extract the last line and remove "0x"
        if result.returncode == 0:
            last_line = result.stdout.strip().split("\n")[-1]  # Get last line
            cleaned_address = last_line[2:] if last_line.startswith("0x") else last_line  # Remove "0x" prefix
            
            print(f"Extracted Node Address: {cleaned_address}\n")

            # Load existing addresses from JSON file if it exists
            if os.path.exists(self.validator_addresses):
                with open(self.validator_addresses, "r") as file:
                    try:
                        addresses = json.load(file)  # Read existing list
                        if not isinstance(addresses, list):
                            addresses = []
                    except json.JSONDecodeError:
                        addresses = []
            else:
                addresses = []
                

            # Append the new address if it's not already in the list
            if cleaned_address not in addresses:
                addresses.append(cleaned_address)

                # Save updated address list to JSON file
                with open(self.validator_addresses, "w") as file:
                    json.dump(addresses, file, indent=4)
                print(f"Node address saved in `{self.validator_addresses}` as JSON\n")
            else:
                print("Address already exists in JSON file. Skipping update.\n")

            return cleaned_address
        else:
            print(f"Error extracting node address: {result.stderr}")
            return None
        
    #---------------------Extra data generation----------------------------
    def update_extra_data_in_genesis(self):
            """Encodes validators into RLP and updates extraData in genesis.json."""

            extra_data_file = os.path.join(self.genesis_files_path, "extraData.rlp")
            
            # Check if validators.json exists
            if not os.path.exists(self.validator_addresses):
                print(f"Error: Validators file `{self.validator_addresses }` not found. Run `update_genesis_file` first.")
                return
            
            # Run Besu command to encode extraData
            encode_result = subprocess.run(
                ["besu", "rlp", "encode", "--from=" + self.validator_addresses, "--to=" + extra_data_file, "--type=QBFT_EXTRA_DATA"],
                capture_output=True, text=True, check=False
            )

            if encode_result.returncode == 0:
                print(f"Encoded extraData saved in `{extra_data_file}`\n")
            else:
                print(f"Error encoding extraData: {encode_result.stderr}\n")
                return

            # Read the generated extraData
            with open(extra_data_file, "r") as file:
                extra_data_rlp = file.read().strip()

            # Load the genesis.json file
            with open(self.genesis_file, "r") as file:
                genesis_data = json.load(file)

            # Update extraData field in genesis.json
            genesis_data["extraData"] = extra_data_rlp

            # Save the updated genesis.json file
            with open(self.genesis_file, "w") as file:
                json.dump(genesis_data, file, indent=4)

            print(f"`extraData` updated in `{self.genesis_file}`\n")


    #---------------------Get the node address besides pub and private key----------------------------
    def get_validator_address(self):
        """Extract Ethereum address from private key using Besu CLI."""
        node_address_file = os.path.join(self.data_path, "node_address.txt") 
        
        result = subprocess.run(
            ["besu", "public-key", "export-address", "--node-private-key-file=" + self.private_key],
            capture_output=True,  # Capture stdout
            text=True,  # Treat output as a string
            check=False  # Do not raise exception on failure
        )

        # Extract only the last line and remove "0x" prefix
        if result.returncode == 0:
            last_line = result.stdout.strip().split("\n")[-1]  # Get last line
            last_line = last_line[2:] if last_line.startswith("0x") else last_line  # Remove "0x" if present

            print(f"Extracted Node Address (without 0x): {last_line}")

            # Save modified node address to file
            with open(node_address_file, "w") as file:
                file.write(last_line + "\n")

            print(f"Node address saved in `{node_address_file}`")
            return last_line
        else:
            print(f"Error extracting node address: {result.stderr}")

    #---------------------Start the blockchain node----------------------------
    def start_blockchain_node(self):
        """Starts the Besu node using subprocess.Popen()"""
        try:
            print("Starting Besu node...")

            process = subprocess.Popen(
                ["besu",
                "--data-path=" + self.data_path,
                "--node-private-key-file=" + self.private_key,
                "--genesis-file=" + self.genesis_file,
                "--rpc-http-enabled",
                "--rpc-http-api=ETH,NET,QBFT, ADMIN, WEB3",
                "--host-allowlist=*",
                "--rpc-http-cors-origins=all"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Read output in real-time
            for line in process.stdout:
                print("[Besu Output]:", line.strip())

            for line in process.stderr:
                print("[Besu Error]:", line.strip())

            process.wait()  # Wait for process to complete

        except FileNotFoundError:
            print("Error: Besu is not installed or not found in PATH.")
        except Exception as e:
            print(f"Unexpected error: {e}")

if __name__ == "__main__":
    blockchain_init = BlockchainInit()
    
    if len(sys.argv) > 1:
        method_name = sys.argv[1]
        method_args = sys.argv[2:]  # Capture additional arguments (if any)

        # Check if the method exists in the class
        if hasattr(blockchain_init, method_name):
            method = getattr(blockchain_init, method_name)

            # Check if it's callable
            if callable(method):
                # Get function argument count (excluding `self`)
                arg_count = method.__code__.co_argcount - 1  # Subtract 1 for `self`
                
                if len(method_args) == arg_count:
                    # Call method dynamically with arguments (if required)
                    method(*method_args)
                elif arg_count == 0:
                    # Call method without arguments
                    method()
                else:
                    print(f"Error: Function '{method_name}' requires {arg_count} argument(s), but {len(method_args)} were given.")
            else:
                print(f"Error: '{method_name}' is not callable.")
        else:
            print(f"Error: Function '{method_name}' not found in BlockchainInit.")
    else:
        print("Usage: python blockchain_init.py <function_name> [arguments...]")