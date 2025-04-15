import requests
import re

class AcknowledgementSender:
    """Sends acknowledgment, enode, and required files to the Fog Node."""

    def __init__(self, registering_node_url, genesis_file, node_registry_file, besu_rpc_url, prefunded_keys_file):
        """
        Initializes the Cloud Node Acknowledgment sender.

        :param registering_node_url: The Fog Node API endpoint for acknowledgment.
        :param genesis_file: Path to the genesis file.
        :param node_registry_file: Path to the node registry file.
        :param besu_rpc_url: Besu JSON-RPC URL to fetch enode.
        """
        self.registering_node_url = registering_node_url
        self.genesis_file = genesis_file
        self.node_registry_file = node_registry_file
        self.besu_rpc_url = besu_rpc_url
        self.prefunded_keys_file = prefunded_keys_file

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
            print("Error: Enode could not be retrieved!")
            return

        data = {
            "node_id": node_id,
            "enode": enode_id  # Include enode in the acknowledgment data
        }
        print(self.registering_node_url)
        print(self.genesis_file)
        print(self.node_registry_file)
        print(self.prefunded_keys_file)

        files = {
            "genesis_file": open(self.genesis_file, "rb"),
            "node_registry_file": open(self.node_registry_file, "rb"),
            "prefunded_keys_file": open(self.prefunded_keys_file, "rb")
        }

        try:
            response = requests.post(f"{self.registering_node_url}/acknowledgement", data=data, files=files)

            if response.status_code == 200:
                print(f"Acknowledgment sent successfully to Node {node_id} with enode: {enode_id}")
            else:
                print(f"Failed to send acknowledgment: {response.json()}")

        except Exception as e:
            print(f"Error sending acknowledgment: {e}")

        finally:
            # Close file handles
            for file in files.values():
                file.close()

