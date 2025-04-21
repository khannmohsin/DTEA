import json
from eth_account import Account
import subprocess
import os
from eth_keys import keys
import sys

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

    #---------------------Start the blockchain node----------------------------
    def start_blockchain_node(self, p2p_port, rpc_http_port):
        enode_address = self.load_enode_address()
        with open(self.prefunded_account_file, "r") as f:
            data = json.load(f)
        addresses = [entry["address"] for entry in data["prefunded_accounts"]]
        for addr in addresses:
            print(f"Address: {addr}")  

        first_address = addresses[0]

        """Starts the Besu node using subprocess.Popen()"""
        try:
            print("Starting Besu node...")

            process = subprocess.Popen(
                ["besu",
                "--data-path=" + self.data_path,
                "--node-private-key-file=" + self.private_key,
                "--genesis-file=" + self.genesis_file,
                "--bootnodes=" + enode_address,
                "--p2p-port=" + str(p2p_port),
                "--rpc-http-enabled",
                "--rpc-http-api=ETH,NET,QBFT,ADMIN,WEB3,TXPOOL,MINER",
                "--host-allowlist=*",
                "--miner-enabled",
                "--miner-coinbase=" + first_address,
                "--rpc-http-cors-origins=all",
                "--min-gas-price=0",
                "--rpc-http-port=" + str(rpc_http_port)],
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
                arg_count = method.__code__.co_argcount - 1 
                
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





