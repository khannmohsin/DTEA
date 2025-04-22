import requests
import re

class AcknowledgementSender:

    def __init__(self, registering_node_url, genesis_file, node_registry_file, besu_rpc_url, prefunded_keys_file, enode_file):

        self.registering_node_url = registering_node_url
        self.genesis_file = genesis_file
        self.node_registry_file = node_registry_file
        self.besu_rpc_url = besu_rpc_url
        self.prefunded_keys_file = prefunded_keys_file
        self.enode_file = enode_file

    def get_enode(self):
        payload = {
            "jsonrpc": "2.0",
            "method": "admin_nodeInfo",
            "params": [],
            "id": 1
        }

        try:
        #     with open(self.enode_file, "r") as file:
        #         enode_url = file.read().strip()
            response = requests.post(self.besu_rpc_url, json=payload, headers={"Content-Type": "application/json"})
            data = response.json()
            enode_url = data.get("result", {}).get("enode", "")

            if enode_url:
                
                match = re.match(r"enode://([a-fA-F0-9]+)@([\d\.]+):(\d+)", enode_url)
                if match:
                    return enode_url  
            return None
        
        except requests.exceptions.RequestException as e:
            print(f"Error fetching enode: {e}")
            return None
        # except FileNotFoundError as e:
        #     print(f"Error: {e}")
        #     return None
        # except Exception as e:
        #     print(f"Unexpected error: {e}")
        #     return None


    def send_acknowledgment(self, node_id):        
        enode_id = self.get_enode()
        if not enode_id:
            print("Error: Enode could not be retrieved!")
            return

        data = {
            "node_id": node_id,
            "enode": enode_id  
        }

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
            for file in files.values():
                file.close()




