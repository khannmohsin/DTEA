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
        self.node_type = node_type 
        self.rpc_URL =  rpc_url
        self.registration_url = registration_url 
        self.root_path = os.path.dirname(os.path.abspath(__file__)) 
        self.public_key = self.load_public_key(key_path) 
        self.node_url = node_url  
        self.private_key =  os.path.join(self.root_path, "data/key.priv")
        self.address = self.get_address() 

    def get_address(self):
        result = subprocess.run(
            ["besu", "public-key", "export-address", "--node-private-key-file=" + self.private_key],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            last_line = result.stdout.strip().split("\n")[-1]  # Get last line
            
            print(f"\nExtracted Node Address: {last_line}\n")

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

        """Send Public Key & Metadata to Cloud API for Registration."""
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
            print(f"{self.node_type.capitalize()} Node '{self.node_name}' (ID: {self.node_id}) Registered Successfully!")
            print(f"Public Key Sent: {self.public_key}")
            print(f"Node Address: {self.address}")
            with open("node-details.json", "w") as json_file:
                json.dump(data, json_file, indent=4)
            print(response.json())
        else:
            
            print(f"Error Registering Node '{self.node_name}' (ID: {self.node_id}):")
            print(json.dumps(response.json(), indent=4))

    def read_data(self):

        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  
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
        
    def remove_data(self):
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type, 
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Transmit data to the Cloud Node."""
        response = requests.delete(f"{self.registration_url}/remove", params=data)

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
                print(f"Error Reading Data (Status {response.status_code}): {response.text}")
                return None
            
        except ValueError:
            print("Error: Response was not valid JSON")
            print("Raw response:", response.text)
            return None
        
    def update_data(self):
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Execute a command on the Cloud Node."""
        response = requests.put(f"{self.registration_url}/update", params=data)

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
            

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "register":
            if len(sys.argv) != 9:
                print("Usage: python root_node_reg_request.py register <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>, <node_url>, <rpc_url>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path, reg_node_url, rpc_url = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, reg_node_url, rpc_url)
            node.register_node()

        elif command == "read":
            if len(sys.argv) != 7:
                print("Usage: python root_node_reg_request.py read <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            data = node.read_data()

        elif command == "write":
            if len(sys.argv) != 7:
                print("Usage: python root_node_reg_request.py write <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            node.write_data()

        elif command == "remove":
            if len(sys.argv) != 7:
                print("Usage: python root_node_reg_request.py remove <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            node.remove_data()

        elif command == "update":
            if len(sys.argv) != 7:
                print("Usage: python root_node_reg_request.py update <node_id>, <node_name>, <node_type>, <registration_url>, <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, registration_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, registration_url, key_path, "", "")
            node.update_data()

        else:
            print(f"Error: Unknown command '{command}'")
    else:
        print("Usage: python root_node_reg_request.py <command> [arguments...]")






        

