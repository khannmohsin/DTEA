import requests
import os
import subprocess
from fog_blockchain_init import BlockchainInit
from fog_node_registration import NodeRegistry

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

    def register_with_cloud(self):
        """Send Public Key & Metadata to Cloud API for Registration."""
        data = {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,  # Node type added
            "public_key": self.public_key,
            "address": self.address
        }

        response = requests.post(f"{self.cloud_url}/register-node", json=data)
        if response.status_code == 200:
            print(f"{self.node_type.capitalize()} Node {self.node_id} Registered Successfully as '{self.node_name}'!")
            print(f"Public Key Sent: {self.public_key}")
            print(f"Node Address: {self.address}")
        else:
            print(f"Error Registering {self.node_type.capitalize()} Node {self.node_id}: {response.json()}")


# Example: Register a Fog Node
if __name__ == "__main__":
    cloud_api_url = "http://127.0.0.1:5000"  # Update with actual Cloud Node IP
    key_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"
    generate_keys = BlockchainInit()
    gen_keys = generate_keys.generate_keys()
    # Example: Register a Fog Node
    fog_node = Node(node_id="FOG-05", node_name="FogDevice-05", node_type="Fog", cloud_url=cloud_api_url, key_path=key_path)
    fog_node.register_with_cloud()





        

