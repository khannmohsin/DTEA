import requests
import os

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
            "public_key": self.public_key
        }

        response = requests.post(f"{self.cloud_url}/register-node", json=data)
        if response.status_code == 200:
            print(f"{self.node_type.capitalize()} Node {self.node_id} Registered Successfully as '{self.node_name}'!")
            print(f"Public Key Sent: {self.public_key}")
        else:
            print(f"Error Registering {self.node_type.capitalize()} Node {self.node_id}: {response.json()}")

