#!/usr/bin/env python3

import requests
import os
import subprocess
import sys
import time
import json  # <-- Added for signing
from eth_keys import keys  # <-- Added for signing
from eth_utils import keccak

class Node:
    def __init__(self, node_id, node_name, node_type, registration_url, key_path, node_url, rpc_url):

        self.node_id = node_id
        self.node_name = node_name
        self.node_type = node_type  # Generalized for any node type
        self.rpc_URL =  rpc_url
        self.registration_url = registration_url  # Cloud Node API URL
        self.public_key = self.load_public_key(key_path)  # Load Public Key from file
        self.node_url = node_url  # URL of the node
        self.root_path = os.path.dirname(os.path.abspath(__file__))
        self.private_key =  os.path.join(self.root_path, "data/key.priv")
        self.address = self.get_address()  # Get the address of the node

    def get_address(self):
        result = subprocess.run(
            ["besu", "public-key", "export-address", "--node-private-key-file=" + self.private_key],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            last_line = result.stdout.strip().split("\n")[-1]  # Get last line
            
            print(f"Extracted Node Address: {last_line}\n")

            return last_line

    def load_public_key(self, key_path):
        """Read the stored public key from key.pub file."""
        if os.path.exists(key_path):
            with open(key_path, "r") as key_file:
                return key_file.read().strip()
        else:
            raise FileNotFoundError(f"Public Key File Not Found: {key_path}")
        

    def sign_identity(self):
        message_dict = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,
            "public_key": self.public_key
        }
        message_json = json.dumps(message_dict, sort_keys=True)
        message_hash = keccak(text=message_json)

        with open(self.private_key, "r") as f:
            private_key_hex = f.read().strip()

        if private_key_hex.startswith("0x"):
            private_key_hex = private_key_hex[2:]

        private_key = keys.PrivateKey(bytes.fromhex(private_key_hex))
        signature = private_key.sign_msg_hash(message_hash)
        return signature.to_hex()
    
    
    def register_node(self):

        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  
            "public_key": self.public_key,
            "address": self.address,
            "node_url": self.node_url,
            "rpcURL" : self.rpc_URL,
            "signature": self.sign_identity()
        }

        response = requests.post(f"{self.registration_url}/register-node", json=data)
        if response.status_code == 200:
            print(f"{self.node_type.capitalize()} Node {self.node_id} Registered Successfully as '{self.node_name}'!")
            print(f"Public Key Sent: {self.public_key}")
            print(f"Node Address: {self.address}")
            with open("node-details.json", "w") as json_file:
                json.dump(data, json_file, indent=4)
            print(response.json())
        else:

            print(f"\nError Registering {self.node_type.capitalize()} \nNode {self.node_id}: {response.json()}")

    def read_data(self):

        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }

        """Read data from the accessed Node."""
        response = requests.get(f"{self.registration_url}/read", params=data)

        try:
            if response.status_code == 200:
                print("Data:", response.json())
                return response.json()
            else:
                print(f"Error Reading Data (Status {response.status_code}): {response.text}")
                return None
            
        except ValueError:
            print("Error: Response was not valid JSON")
            print("Raw response:", response.text)
            return None
        
    def transmit_data(self):
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Transmit data to the Cloud Node."""
        response = requests.post(f"{self.registration_url}/transmit", params=data)

        try:
            if response.status_code == 200:
                print("Data:", response.json())
                return response.json()
            else:
                print(f"Error Transmitting Data (Status {response.status_code}): {response.text}")
                return None
            
        except ValueError:
            print("Error: Response was not valid JSON")
            print("Raw response:", response.text)
            return None
        
    def write_data(self):
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Transmit data to the Cloud Node."""
        response = requests.post(f"{self.registration_url}/write", params=data)

        try:
            if response.status_code == 200:
                print("Data:", response.json())
                return response.json()
            else:
                print(f"Error Writing Data (Status {response.status_code}): {response.text}")
                return None
            
        except ValueError:
            print("Error: Response was not valid JSON")
            print("Raw response:", response.text)
            return None
        
    def execute_command(self):
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Execute a command on the Registering Node."""
        response = requests.post(f"{self.registration_url}/execute", params=data)

        try:
            if response.status_code == 200:
                print("Data:", response.json())
                return response.json()
            else:
                print(f"Error Executing Command (Status {response.status_code}): {response.text}")
                return None
            
        except ValueError:
            print("Error: Response was not valid JSON")
            print("Raw response:", response.text)
            return None
        

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "register":
            if len(sys.argv) != 9:
                print("Usage: python client_node_reg_request.py register <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>, <node_url>, <rpc_url>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path, reg_node_url, rpc_url = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, reg_node_url, rpc_url)
                        # "$node_id" "$node_name" "$node_type" "$root_url" "$key_path" "$NODE_URL" "$rpc_url"
            node.register_node()

        elif command == "read":
            if len(sys.argv) != 7:
                print("Usage: python client_node_reg_request.py read <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            data = node.read_data()

        elif command == "write":
            if len(sys.argv) != 7:
                print("Usage: python client_node_reg_request.py write <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            node.write_data()

        elif command == "transmit":
            if len(sys.argv) != 7:
                print("Usage: python client_node_reg_request.py transmit <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            node.transmit_data()

        elif command == "execute":
            if len(sys.argv) != 7:
                print("Usage: python client_node_reg_request.py execute <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            node.execute_command()

        else:
            print(f"Error: Unknown command '{command}'")
    else:
        print("Usage: python fog_node.py <command> [arguments...]")





        

