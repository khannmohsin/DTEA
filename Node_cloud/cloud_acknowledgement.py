import requests
import json
import re

class CloudAcknowledgementSender:
    """Sends acknowledgment, enode, and required files to the Fog Node."""

    def __init__(self, fog_node_url, genesis_file, node_registry_file, besu_rpc_url):
        """
        Initializes the Cloud Node Acknowledgment sender.

        :param fog_node_url: The Fog Node API endpoint for acknowledgment.
        :param genesis_file: Path to the genesis file.
        :param node_registry_file: Path to the node registry file.
        :param besu_rpc_url: Besu JSON-RPC URL to fetch enode.
        """
        self.fog_node_url = fog_node_url
        self.genesis_file = genesis_file
        self.node_registry_file = node_registry_file
        self.besu_rpc_url = besu_rpc_url

    def get_enode(self):
        """Fetches the full enode URL from the Besu blockchain."""
        payload = {
            "jsonrpc": "2.0",
            "method": "admin_nodeInfo",
            "params": [],
            "id": 1
        }

        try:
            response = requests.post(self.besu_rpc_url, json=payload, headers={"Content-Type": "application/json"})
            data = response.json()

            # Extract enode URL
            enode_url = data.get("result", {}).get("enode", "")

            if enode_url:
                # Ensure enode URL follows correct format
                match = re.match(r"enode://([a-fA-F0-9]+)@([\d\.]+):(\d+)", enode_url)
                if match:
                    return enode_url  # Return the full enode URL
            return None

        except requests.exceptions.RequestException as e:
            print(f"Error fetching enode: {e}")
            return None
        

    def send_acknowledgment(self, node_id):
        """Sends acknowledgment, enode, and files to the Fog Node."""
        
        # Fetch enode ID
        enode_id = self.get_enode()
        if not enode_id:
            print("❌ Error: Enode could not be retrieved!")
            return

        data = {
            "node_id": node_id,
            "enode": enode_id  # Include enode in the acknowledgment data
        }

        files = {
            "genesis_file": open(self.genesis_file, "rb"),
            "node_registry_file": open(self.node_registry_file, "rb")
        }

        try:
            response = requests.post(f"{self.fog_node_url}/acknowledgement", data=data, files=files)

            if response.status_code == 200:
                print(f"✅ Acknowledgment sent successfully to Node {node_id} with enode: {enode_id}")
            else:
                print(f"❌ Failed to send acknowledgment: {response.json()}")

        except Exception as e:
            print(f"❌ Error sending acknowledgment: {e}")

        finally:
            # Close file handles
            for file in files.values():
                file.close()


# Define Fog Node API URL
FOG_NODE_URL = "http://127.0.0.1:5001"  # Update with actual Fog Node URL

# Define file paths
GENESIS_FILE_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/genesis.json"
NODE_REGISTRY_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"

# Besu RPC URL
BESU_RPC_URL = "http://127.0.0.1:8545"

# Initialize Acknowledgment Sender
cloud_ack_sender = CloudAcknowledgementSender(FOG_NODE_URL, GENESIS_FILE_PATH, NODE_REGISTRY_PATH, BESU_RPC_URL)

# Send Acknowledgment (Example)
cloud_ack_sender.send_acknowledgment(
    node_id="FN-002"
)