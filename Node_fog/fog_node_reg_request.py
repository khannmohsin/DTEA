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
    def __init__(self, node_id, node_name, node_type, cloud_url, key_path):
        """
        Initialize the Node with its ID, Name, Type, and Cloud API URL.

        :param node_id: Unique identifier for the node
        :param node_name: Human-readable name for the node
        :param node_type: Type of node (sensor, edge, fog, cloud, actuator)
        :param cloud_url: API endpoint of the cloud server for registration
        :param key_path: Path to the public key file
        """
        self.node_id = node_id
        self.node_name = node_name
        self.node_type = node_type  # Generalized for any node type
        self.cloud_url = cloud_url  # Cloud Node API URL
        self.public_key = self.load_public_key(key_path)  # Load Public Key from file
        self.private_key = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.priv"
        # uncomment after testing
        # self.address = "e39035c0c9ae42348fdd6325f12787c862a88dae"
        self.address = self.get_address()  # Get the address of the node

    def get_address(self):
        result = subprocess.run(
            ["besu", "public-key", "export-address", "--node-private-key-file=" + self.private_key],
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            last_line = result.stdout.strip().split("\n")[-1]  # Get last line
            cleaned_address = last_line[2:] if last_line.startswith("0x") else last_line  # Remove "0x" prefix
            
            print(f"Extracted Node Address: {cleaned_address}\n")

            return cleaned_address

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
    
    
    def register_with_cloud(self):

    

        """Send Public Key & Metadata to Cloud API for Registration."""
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }

        response = requests.post(f"{self.cloud_url}/register-node", json=data)
        print(response)
        if response.status_code == 200:
            print(f"{self.node_type.capitalize()} Node {self.node_id} Registered Successfully as '{self.node_name}'!")
            print(f"Public Key Sent: {self.public_key}")
            print(f"Node Address: {self.address}")
        else:
            print(f"Error Registering {self.node_type.capitalize()} Node {self.node_id}: {response.json()}")

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
        response = requests.get(f"{self.cloud_url}/read", params=data)

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
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Transmit data to the Cloud Node."""
        response = requests.post(f"{self.cloud_url}/transmit", params=data)

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
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Transmit data to the Cloud Node."""
        response = requests.post(f"{self.cloud_url}/write", params=data)

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
        
    def execute_command(self):
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address,
            "signature": self.sign_identity()
        }
        """Execute a command on the Cloud Node."""
        response = requests.post(f"{self.cloud_url}/execute", params=data)

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
            

    # def transmit_data(self):
    #     """Transmit data to the Cloud Node."""            

    # def execute_command(self):
    #     """Execute a command on the Cloud Node."""
    #     response = requests.post(f"{self.cloud_url}/execute", json={"command": command})
    #     if response.status_code == 200:
    #         print("Command Executed Successfully!")
    #     else:
    #         print(f"Error Executing Command: {response.json()}")

    # def write_data(self, data):
    #     """Write data to the Cloud Node."""
    #     response = requests.post(f"{self.cloud_url}/write", json=data)
    #     if response.status_code == 200:
    #         print("Data Written Successfully!")
    #     else:
    #         print(f"Error Writing Data: {response.json()}")

# Example: Register a Fog Node
if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "register":
            if len(sys.argv) != 7:
                print("Usage: python fog_node.py register <node_id> <node_name> <node_type> <cloud_url> <key_path>")
                sys.exit(1)

            node_id, node_name, node_type, cloud_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, cloud_url, key_path)
            node.register_with_cloud()

        elif command == "read":
            if len(sys.argv) != 7:
                print("Usage: python fog_node.py read <cloud_url>")
                sys.exit(1)

            node_id, node_name, node_type, cloud_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, cloud_url, key_path)
            data = node.read_data()

        elif command == "write":
            if len(sys.argv) != 7:
                print("Usage: python fog_node.py write <cloud_url> <data>")
                sys.exit(1)

            node_id, node_name, node_type, cloud_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, cloud_url, key_path)
            node.write_data()

        elif command == "transmit":
            if len(sys.argv) != 7:
                print("Usage: python fog_node.py transmit <cloud_url> <data>")
                sys.exit(1)

            node_id, node_name, node_type, cloud_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, cloud_url, key_path)
            node.transmit_data()

        elif command == "execute":
            if len(sys.argv) != 7:
                print("Usage: python fog_node.py execute <cloud_url> <data>")
                sys.exit(1)

            node_id, node_name, node_type, cloud_url, key_path = sys.argv[2:]
            node = Node(node_id, node_name, node_type, cloud_url, key_path)
            node.execute_command()

        else:
            print(f"Error: Unknown command '{command}'")
    else:
        print("Usage: python fog_node.py <command> [arguments...]")

    # cloud_api_url = "http://127.0.0.1:5000"  # Update with actual Cloud Node IP
    # key_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"
    # # Example: Register a Fog Node
    # fog_node = Node(node_id="FOG-001", node_name="FogDevice-001", node_type="Fog", cloud_url=cloud_api_url, key_path=key_path)
    # fog_node.register_with_cloud()





        

