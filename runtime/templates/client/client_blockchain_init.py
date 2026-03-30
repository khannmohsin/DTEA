import json
from eth_account import Account
from monitor import track_performance
import subprocess
import os
from eth_keys import keys
import sys
import inspect

class BlockchainInit:
    def __init__(self):
        self.root_path = os.path.dirname(os.path.abspath(__file__))
        self.prefunded_account_file = os.path.join(self.root_path, "prefunded_keys.json")
        # self.config_file = os.path.join(self.root_path, "qbftConfigFile.json")
        self.data_path = os.path.join(self.root_path, "data/")
        self.genesis_files_path = os.path.join(self.root_path, "genesis/")
        self.private_key = os.path.join(self.data_path, "key.priv")
        self.public_key = os.path.join(self.data_path, "key.pub")
        # self.validator_addresses = os.path.join(self.genesis_files_path, "validator_address.json")
        self.genesis_file = os.path.join(self.genesis_files_path, "genesis.json")
        self.enode_file = os.path.join(self.data_path, "enode.txt") 
        # self.enode_address = self.load_enode_address()

        
    #---------------------Load Enode address---------------------------- 
    @track_performance
    def load_enode_address(self):
        """Reads the enode address from the .txt file"""
        if os.path.exists(self.enode_file):
            with open(self.enode_file, "r") as file:
                enode = file.read().strip()  
                print(f"Loaded Enode Address: {enode}")  
                return enode
        else:
            print(f"Error: Enode file {self.enode_file} not found.")
            return None

    #---------------------Node Public and Private generation----------------------------
    @track_performance
    def generate_keys(self):
        """Generates a new Node keys (private key and public key)."""
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
    def generate_account(self, count: int = 1):
        """
        Generates `count` new Ethereum accounts and saves them to
        self.prefunded_account_file as:
        {
            "prefunded_accounts": [
            {"private_key": "<hex>", "address": "0x..."},
            ...
            ]
        }
        If the file exists, new accounts are appended.
        """
        count = int(count)
        new_entries = []

        for _ in range(count):
            acct = Account.create()
            # eth-account returns a LocalAccount; its private key can be accessed as:
            #  - acct._private_key (HexBytes) in some versions
            #  - acct.key (bytes) in others
            # We'll normalize to 0x-hex string safely:
            pk_hex = getattr(acct, "_private_key", None)
            if pk_hex is None:
                pk_hex = getattr(acct, "key")  # bytes
            pk_hex = pk_hex.hex() if hasattr(pk_hex, "hex") else str(pk_hex)
            if not pk_hex.startswith("0x"):
                pk_hex = pk_hex

            new_entries.append({
                "private_key": pk_hex,     # keep 0x prefix (safer for web3)
                "address": acct.address
            })

        # Read existing file (if any) and append
        existing = {"prefunded_accounts": []}
        if os.path.exists(self.prefunded_account_file):
            try:
                with open(self.prefunded_account_file, "r") as f:
                    existing = json.load(f) or existing
                    if "prefunded_accounts" not in existing:
                        existing["prefunded_accounts"] = []
            except Exception:
                existing = {"prefunded_accounts": []}

        existing["prefunded_accounts"].extend(new_entries)

        os.makedirs(os.path.dirname(self.prefunded_account_file), exist_ok=True)
        with open(self.prefunded_account_file, "w") as f:
            json.dump(existing, f, indent=2)
            

    #---------------------Start the blockchain node----------------------------
    @track_performance
    def start_blockchain_node(self, p2p_port, rpc_http_port, ip_address):
        enode_address = self.load_enode_address()
        with open(self.prefunded_account_file, "r") as f:
            data = json.load(f)
        addresses = [entry["address"] for entry in data["prefunded_accounts"]]

        first_address = addresses[0]

        """Starts the Besu node using subprocess.Popen()"""
        try:
            print("Starting Besu node...")

            process = subprocess.Popen(
                ["besu",
                "--data-path=" + self.data_path,
                "--node-private-key-file=" + self.private_key,
                "--genesis-file=" + self.genesis_file,
                "--network-id=1337",
                "--bootnodes=" + enode_address,
                "--p2p-port=" + str(p2p_port),
                "--rpc-http-enabled",
                "--rpc-http-host=0.0.0.0",
                "--rpc-http-api=ETH,NET,QBFT,ADMIN,WEB3,TXPOOL,MINER",
                "--host-allowlist=*",
                "--rpc-http-cors-origins=all",
                "--min-gas-price=0",
                "--rpc-http-port=" + str(rpc_http_port),
                "--p2p-host=" + str(ip_address),
                # ✅ Metrics flags for monitoring
                "--metrics-enabled",
                "--metrics-host=0.0.0.0",
                "--metrics-port=9652",  # runtime fog1 metrics port
                "--metrics-category=BLOCKCHAIN,JVM,NETWORK,RPC,TRANSACTION_POOL,PEERS,SYNCHRONIZER,ETHEREUM,PERMISSIONING,PROCESS"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            for line in process.stdout:
                print("[Besu Output]:", line.strip())

            for line in process.stderr:
                print("[Besu Error]:", line.strip())

            process.wait()  

        except FileNotFoundError:
            print("Error: Besu is not installed or not found in PATH.")
        except Exception as e:
            print(f"Unexpected error: {e}")

if __name__ == "__main__":
    blockchain_init = BlockchainInit()
    
    if len(sys.argv) > 1:
        method_name = sys.argv[1]
        method_args = sys.argv[2:] 

        if hasattr(blockchain_init, method_name):
            method = getattr(blockchain_init, method_name)

            if callable(method):
                # arg_count = method.__code__.co_argcount - 1 
                arg_count = len(inspect.signature(method).parameters)
                
                if len(method_args) == arg_count:
                    method(*method_args)
                elif arg_count == 0:
                    method()
                else:
                    print(f"Error: Function '{method_name}' requires {arg_count} argument(s), but {len(method_args)} were given.")
            else:
                print(f"Error: '{method_name}' is not callable.")
        else:
            print(f"Error: Function '{method_name}' not found in BlockchainInit.")
    else:
        print("Usage: python blockchain_init.py <function_name> [arguments...]")



