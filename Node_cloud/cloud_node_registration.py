from flask import Flask, request, jsonify
from cloud_acknowledgement import CloudAcknowledgementSender
from cloud_blockchain_init import BlockchainInit
import json
import os
import subprocess
import time
from eth_keys import keys
from eth_utils import keccak
import re

class NodeRegistry:
    """Class to manage the registration and retrieval of nodes (Cloud, Fog, Edge, Sensor)."""

    def __init__(self, filename="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/unregistered_nodes.json"):
        """Initialize with the JSON file storing node data and set up Flask."""
        self.filename = filename
        self.nodes = self.load_nodes()
        self.app = Flask(__name__)
        self.setup_routes()                

    def verify_node_identity(self, data):
        try:
            signature_hex = data.get("signature")
            public_key_hex = data.get("public_key")

            # Create the original message (must match what the node signed)
            message_dict = {
                "node_id": data.get("node_id"),
                "node_name": data.get("node_name"),
                "node_type": data.get("node_type"),
                "public_key": public_key_hex
            }

            message_json = json.dumps(message_dict, sort_keys=True)
            message_hash = keccak(text=message_json)

            if public_key_hex.startswith("0x"):
                public_key_hex = public_key_hex[2:]
            if signature_hex.startswith("0x"):
                signature_hex = signature_hex[2:]

            public_key = keys.PublicKey(bytes.fromhex(public_key_hex))
            signature = keys.Signature(bytes.fromhex(signature_hex))

            return public_key.verify_msg_hash(message_hash, signature)

        except Exception as e:
            print("Signature verification failed:", str(e))
            return False

    def register_node_on_chain(self, node_id, node_name, node_type, public_key, address, receiver_node_type, signature):
        try:
            result = subprocess.run([
                "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "registerNode",
                node_id, node_name, node_type, public_key, address, receiver_node_type, signature
            ], capture_output=True, text=True)

            output = result.stdout.strip()

            # Ensure always returning 3 values
            if result.returncode != 0:
                return "error", result.stderr.strip(), output  

            # Changed: return 3 values to match expected unpacking
            return "success", "Node registered", output

        except Exception as e:
            return "error", f"Exception occurred: {str(e)}", ""  
        
    def is_node_registered_js(self, nodeSignature):
        try:
            result = subprocess.run(
                ["node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "isNodeRegistered", nodeSignature],
                capture_output=True,
                text=True
            )

            # print(result)
            print("-----------")

            output = result.stdout.strip()
            if output == "true":
                return {"status": "success", "registered": True}, 200
            elif output == "false":
                return {"status": "success", "registered": False}, 200
            else:
                print("Unexpected output:", output)
                return {"status": "error", "message": f"Unexpected output: {output}"}, 500

        except Exception as e:
            return {"status": "error", "message": str(e)}, 500
        
    def get_node_details_js(self, nodeSignature):
        try:
            result = subprocess.run(
                ["node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "getNodeDetails", nodeSignature],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return {"status": "error", "message": result.stderr.strip()}, 500

            output = result.stdout.strip()
            
            try:
                # Attempt to parse the JSON output from JS
                details = json.loads(output)
                return {"status": "success", "details": details}, 200
            except json.JSONDecodeError:
                return {"status": "error", "message": "Invalid JSON from JS", "raw_output": output}, 500

        except Exception as e:
            return {"status": "error", "message": str(e)}, 500
    

    def load_nodes(self):
        """Load existing node data from the JSON file."""
        if os.path.exists(self.filename):
            with open(self.filename, "r") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    def save_nodes(self):
        """Save node data to JSON file."""
        with open(self.filename, "w") as f:
            json.dump(self.nodes, f, indent=4)

    def register_node(self, node_id, node_name, node_type, public_key, address):
        """
        Register a node in the network.
        :param node_id: Unique Node ID (e.g., FN-001, CN-001)
        :param node_name: Name of the node
        :param node_type: Type of the node (Cloud, Fog, Edge, Sensor)
        :param public_key: Public key of the node
        :return: JSON response with status
        """
        
        if node_id in self.nodes:
            return {"status": "error", "message": "Node already registered"}, 409
        
        self.nodes[node_id] = {
            "node_name": node_name,
            "node_type": node_type,
            "public_key": public_key,
            "address": address
        }
        self.save_nodes()
        return {"status": "approved", "message": f"{node_type} Node registered", "node_id": node_id}, 200

    def get_nodes(self, node_type=None):
        """
        Retrieve all registered nodes or filter by node type.
        :param node_type: Optional filter for node type
        :return: List of nodes or nodes of a specific type
        """
        if node_type:
            filtered_nodes = {k: v for k, v in self.nodes.items() if v["node_type"] == node_type}
            return filtered_nodes
        return self.nodes

    def add_validator_address(self, address):
        """
        Add a validator address to the JSON file.
        :param address: Validator address to be added
        :return: JSON response with status
        """
        validator_file = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/validator_address.json"
        
        if os.path.exists(validator_file):
            with open(validator_file, "r") as f:
                try:
                    addresses = json.load(f)
                except json.JSONDecodeError:
                    addresses = []
        else:
            addresses = []

        if address in addresses:
            return {"status": "error", "message": "Address already exists"}, 409

        addresses.append(address)
        
        with open(validator_file, "w") as f:
            json.dump(addresses, f, indent=4)
        
        return {"status": "approved", "message": "Validator address added"}, 200
    
    def check_smart_contract(self):
        node_registry_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"

        if os.path.exists(node_registry_path):
            print("Node registry file exists. Smart contract can be checked onchain.")
            return True
        else:
            print("Node registry file does not exist. Smart contract cannot be checked onchain.")
            # If the node registry file doesn't exist, we assume the smart contract is not deployed
            return False
        
    def check_smart_contract_deployment(self):
            result = subprocess.run([
                "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "checkIfDeployed",
            ], capture_output=True, text=True)
            
            output = result.stdout.strip()

            if output == "true":
                print("Smart contract is correctly deployed.")
                return True
            elif output == "false":
                print("Older version of smart contract deployed. Update Contract address.")
                return False
            else:
                print("Unexpected output:", output)
                return False
            
    def checkValidator(self, node_Signature):
        result = subprocess.run([
            "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "isValidator", node_Signature
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        if output == "true":
            return True
        else:
            return False
        
    def proposeValidator(self, address, add):
        print("Lets check if the address is correct or not")
        # print(address)
        result = subprocess.run([
            "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js",
            "proposeValidatorVote", address, add
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output proposeValidatorVote:", output)

        return output
        
    def emitValidatorProposalToChain(self, address):
        result = subprocess.run([
            "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js",
            "emitValidatorProposalToChain", address
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output emitValidatorProposalToChain:", output)
        return output
        
    def listenForValidatorProposal(self):
        result = subprocess.run([
            "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js",
            "listenForValidatorProposal"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output listenForValidatorProposal:", output)
        return output

    def get_all_validators(self):

        result = subprocess.run([
            "node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "getValidatorsByBlockNumber"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        # print("Raw JS Output getAllValidators:", output)

        return output
        

    def setup_routes(self):
        GENESIS_FILE_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/genesis.json"
        NODE_REGISTRY_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"
        BESU_RPC_URL = "http://127.0.0.1:8545"
        FOG_NODE_URL = "http://127.0.0.1:5001" 
        """Setup Flask API routes inside the class."""

        @self.app.route("/register-node", methods=["POST"])
        def register_node():

            check_smart_contract = self.check_smart_contract()

            if check_smart_contract:
                print("Received Node Registration Request")
                if self.check_smart_contract_deployment():
                    print("Received Node Registration Request")
                    # return jsonify({"status": "success", "message": "Smart contract is deployed.... \n Proceed with registration"}), 200
                else:
                    return jsonify({"status": "error", "message": "Older version of smart contract deployed. Update required by admin."}), 500
            else:
                return jsonify({"status": "error", "message": "Smart contract not deployed.... \n Wait for admin to upload Smart Contract..."}), 500
            
            data = request.json
            print("Received Node Data:")
            for key, value in data.items():
                print(f"{key}: {value}")

            verify_result = self.verify_node_identity(data)

            if verify_result is True:
                print("Signature Verification Successful. The node is valid.")
                print("Lets check if the node is registered on the blockchain or not")

                # Check if the node is registered on the blockchain
                result, status_code = self.is_node_registered_js(data["signature"])
                print("Node Registration Check Result:", result)

                if result["registered"] == True:
                    print("Node is already registered on the blockchain.")
                    return jsonify({"status": "error", "message": "Node already registered on the blockchain"}), 409
                else:
                    print("Node is not registered on the blockchain. Proceeding with registration.")
                    # return jsonify({"status": "success", "message": "Node need to be registered on the blockchain"}), 409
                    # Register the node on the blockchain
                    status, message, raw_output = self.register_node_on_chain(
                        data["node_id"], data["node_name"], data["node_type"], data["public_key"],
                        data["address"], "Cloud", data["signature"]
                    )

                    # print(status, message, raw_output)
                    if status == "success":
                        print("Node registered successfully on the blockchain.\n \n ")
                        print("Node Registration Output Message:")
                        for line in raw_output.split("\n"):
                            print(line)
                        
                        print(f"Sending acknowledgment to the Fog Node with ID: {data['node_id']}")
                        # blockchain = BlockchainInit()
                        # blockchain.update_extra_data_in_genesis()
                        cloud_ack_sender = CloudAcknowledgementSender(FOG_NODE_URL, GENESIS_FILE_PATH, NODE_REGISTRY_PATH, BESU_RPC_URL)
                        response = jsonify({"status": "success...", "message": "Node registered successfully", "raw_output": raw_output})
                        cloud_ack_sender.send_acknowledgment(node_id="Cloud")
                        
                        # Check if the node is a validator
                        if self.checkValidator(data["signature"]):
                            print("Node is a validator.\n")

                            get_All_validators = self.get_all_validators()
                            print("Current Available:", get_All_validators)

                            print("...  Waiting For some time for the fog node to start the blockchain to proposing it as a validator.  ")
                            time.sleep(60)

                            # Propose the validator
                            # print("Proposing the validator...")
                            # response = self.proposeValidator(data["address"], "true")
                            emitValidatorProposal = self.emitValidatorProposalToChain(data["address"])

                            print("Emit Validator Proposal Response:", emitValidatorProposal)
                            
                            time.sleep(5)
                            listenForValidatorProposal = self.listenForValidatorProposal()
                            print("Listening for Validator Proposal Response:", listenForValidatorProposal)
                            # print("Validator proposal response:", response)

                            time.sleep(5)

                            get_All_validators = self.get_all_validators()
                            print("All Validators:", get_All_validators)
                            while data["address"] not in get_All_validators:
                                print("Validator is not added yet. Waiting for some time.")
                                time.sleep(60)
                                get_All_validators = self.get_all_validators()
                                print("All Validators:", get_All_validators)
                                return jsonify({"status": "error", "message": "Validator is not added yet. Waiting for some time."}), 500
                            
                            return jsonify({"status": "success", "message": "Validator is added to the blockchain."}), 200

                            # Add the validator address to the JSON file
                            # self.add_validator_address(data["address"])
                        else:
                            print("Node is not a validator.")


                        return response, 200                    
                    else:
                        print("Error in registering node on the blockchain:", message)
                        return jsonify({"status": "error", "message": message}), 500
            else:
                print("Signature Verification Failed. The node is invalid.")
                return jsonify({"status": "error", "message": "Signature verification failed"}), 400
                    

        @self.app.route("/read", methods=["GET"])
        def read():
            print("Received Read Request")
            signature = request.args.get("signature")
            node_id = request.args.get("node_id")

            if not signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", signature)

            result, status_code = self.is_node_registered_js(signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:
                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(signature)
                if status == 200:
                    print("Node Details:")
                    print(json.dumps(details["details"], indent=4))  # Pretty print
                    receiver_token = details["details"].get("receiverCapabilityToken", "")
                    permissions = [perm.strip() for perm in receiver_token.split(",")]
                    if "Cloud:READ" in permissions:
                        print("Allowed to read on Cloud:", receiver_token)
                        return jsonify({"status": "success", "message": f"Node {node_id} is allowed to read"}), 200
                    else:
                        print("Not Allowed to read on Cloud:", receiver_token)
                        return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to read"}), 200
                else:
                    print("Error fetching node details:")
                    print(json.dumps(details, indent=4))
                # print("Node Details:", response)
            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404



        @self.app.route("/write", methods=["POST"])
        def write():
            print("Received Read Request")
            signature = request.args.get("signature")
            node_id = request.args.get("node_id")

            if not signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", signature)

            result, status_code = self.is_node_registered_js(signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:
                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(signature)
                if status == 200:
                    print("Node Details:")
                    print(json.dumps(details["details"], indent=4))  # Pretty print
                    receiver_token = details["details"].get("receiverCapabilityToken", "")
                    permissions = [perm.strip() for perm in receiver_token.split(",")]
                    if "Cloud:WRITE" in permissions:
                        print("Allowed to write on Cloud:", receiver_token)
                        return jsonify({"status": "success", "message": f"Node {node_id} is allowed to write"}), 200
                    else:
                        print("Not Allowed to write on Cloud:", receiver_token)
                        return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to write"}), 200
                else:
                    print("Error fetching node details:")
                    print(json.dumps(details, indent=4))
                # print("Node Details:", response)
            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404

        @self.app.route("/execute", methods=["POST"])
        def execute():
            print("Received Read Request")
            signature = request.args.get("signature")
            node_id = request.args.get("node_id")

            if not signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", signature)

            result, status_code = self.is_node_registered_js(signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:
                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(signature)
                if status == 200:
                    print("Node Details:")
                    print(json.dumps(details["details"], indent=4))  # Pretty print
                    receiver_token = details["details"].get("receiverCapabilityToken", "")
                    permissions = [perm.strip() for perm in receiver_token.split(",")]
                    if "Cloud:EXECUTE" in permissions:
                        print("Allowed to execute on Cloud:", receiver_token)
                        return jsonify({"status": "success", "message": f"Node {node_id} is allowed to execute"}), 200
                    else:
                        print("Not Allowed to read on Cloud:", receiver_token)
                        return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to execute"}), 200
                else:
                    print("Error fetching node details:")
                    print(json.dumps(details, indent=4))
                # print("Node Details:", response)
            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404

        @self.app.route("/transmit", methods=["POST"])
        def transmit():
            print("Received Read Request")
            signature = request.args.get("signature")
            node_id = request.args.get("node_id")

            if not signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", signature)

            result, status_code = self.is_node_registered_js(signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:
                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(signature)
                if status == 200:
                    print("Node Details:")
                    print(json.dumps(details["details"], indent=4))  # Pretty print
                    receiver_token = details["details"].get("receiverCapabilityToken", "")
                    permissions = [perm.strip() for perm in receiver_token.split(",")]
                    if "Cloud:TRANSMIT" in permissions:
                        print("Allowed to transmit on Cloud:", receiver_token)
                        return jsonify({"status": "success", "message": f"Node {node_id} is allowed to transmit"}), 200
                    else:
                        print("Not Allowed to transmit on Cloud:", receiver_token)
                        return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to transmit"}), 200
                else:
                    print("Error fetching node details:")
                    print(json.dumps(details, indent=4))
                # print("Node Details:", response)
            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404



        @self.app.route("/get-nodes", methods=["GET"])
        def get_nodes():
            """Retrieve all registered nodes or filter by type."""
            node_type = request.args.get("node_type")
            nodes = self.get_nodes(node_type)
            return jsonify(nodes), 200

    def run(self, host="0.0.0.0", port=5000):
        """Run the Flask application."""
        self.app.run(host=host, port=port)

if __name__ == "__main__":
    registry = NodeRegistry()
    registry.run()

