from node_reg_request import Node
from blockchain_init import BlockchainInit

cloud_api_url = "http://127.0.0.1:5000"  # Update with actual Cloud Node IP
key_path = "/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_fog/data/key.pub"

blockchain = BlockchainInit()
gen_keys = blockchain.generate_keys()

# Example: Register a Fog Node
fog_node = Node(node_id="FN-002", node_name="FogDevice-002", node_type="fog", cloud_url=cloud_api_url, key_path=key_path)
fog_node.register_with_cloud()