from flask import Flask, request, jsonify
from acknowledgement import AcknowledgementSender
import json
import os
import subprocess
import time
import sys
from eth_keys import keys
from eth_utils import keccak

class NodeRegistry:
    """Class to manage the registration and retrieval of nodes (Cloud, Fog, Edge, Sensor)."""

    def __init__(self, besu_RPC_url):
        """Initialize with the JSON file storing node data and set up Flask."""
        self.root_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/"
        self.data_path = os.path.join(self.root_path, "data/")
        self.genesis_files_path = os.path.join(self.root_path, "genesis/")
        self.genesis_file_path = os.path.join(self.genesis_files_path, "genesis.json")
        self.node_registry_path = os.path.join(self.data_path, "NodeRegistry.json")
        self.prefunded_keys_file = os.path.join(self.root_path, "prefunded_keys.json")
        self.interact_file_path = os.path.join(self.root_path, "interact.js")
        self.node_details = os.path.join(self.root_path, "node-details.json")
        self.besu_RPC_url = besu_RPC_url
        # self.registering_node_url = registering_node_url
        # self.besu_RPC_url = "http://127.0.0.1:8545"
        # self.registering_node_url = "http://127.0.0.1:5001" 
        # self.filename = filename
        # self.nodes = self.load_nodes()
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
                "node", self.interact_file_path, "registerNode",
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
                ["node", self.interact_file_path, "isNodeRegistered", nodeSignature],
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
                ["node", self.interact_file_path, "getNodeDetails", nodeSignature],
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
    

    # def load_nodes(self):
    #     """Load existing node data from the JSON file."""
    #     if os.path.exists(self.filename):
    #         with open(self.filename, "r") as f:
    #             try:
    #                 return json.load(f)
    #             except json.JSONDecodeError:
    #                 return {}
    #     return {}

    # def save_nodes(self):
    #     """Save node data to JSON file."""
    #     with open(self.filename, "w") as f:
    #         json.dump(self.nodes, f, indent=4)

    # def register_node(self, node_id, node_name, node_type, public_key, address):
    #     """
    #     Register a node in the network.
    #     :param node_id: Unique Node ID (e.g., FN-001, CN-001)
    #     :param node_name: Name of the node
    #     :param node_type: Type of the node (Cloud, Fog, Edge, Sensor)
    #     :param public_key: Public key of the node
    #     :return: JSON response with status
    #     """
        
    #     if node_id in self.nodes:
    #         return {"status": "error", "message": "Node already registered"}, 409
        
    #     self.nodes[node_id] = {
    #         "node_name": node_name,
    #         "node_type": node_type,
    #         "public_key": public_key,
    #         "address": address
    #     }
    #     self.save_nodes()
    #     return {"status": "approved", "message": f"{node_type} Node registered", "node_id": node_id}, 200

    # def get_nodes(self, node_type=None):
    #     """
    #     Retrieve all registered nodes or filter by node type.
    #     :param node_type: Optional filter for node type
    #     :return: List of nodes or nodes of a specific type
    #     """
    #     if node_type:
    #         filtered_nodes = {k: v for k, v in self.nodes.items() if v["node_type"] == node_type}
    #         return filtered_nodes
    #     return self.nodes

    # def add_validator_address(self, address):
    #     """
    #     Add a validator address to the JSON file.
    #     :param address: Validator address to be added
    #     :return: JSON response with status
    #     """
    #     validator_file = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/validator_address.json"
        
    #     if os.path.exists(validator_file):
    #         with open(validator_file, "r") as f:
    #             try:
    #                 addresses = json.load(f)
    #             except json.JSONDecodeError:
    #                 addresses = []
    #     else:
    #         addresses = []

    #     if address in addresses:
    #         return {"status": "error", "message": "Address already exists"}, 409

    #     addresses.append(address)
        
    #     with open(validator_file, "w") as f:
    #         json.dump(addresses, f, indent=4)
        
    #     return {"status": "approved", "message": "Validator address added"}, 200
    
    def check_smart_contract(self):
        # node_registry_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"

        if os.path.exists(self.node_registry_path):
            print("Node registry file exists. Smart contract can be checked onchain.")
            return True
        else:
            print("Node registry file does not exist. Smart contract cannot be checked onchain.")
            # If the node registry file doesn't exist, we assume the smart contract is not deployed
            return False
        
    def check_smart_contract_deployment(self):
            result = subprocess.run([
                "node", self.interact_file_path, "checkIfDeployed",
            ], capture_output=True, text=True)
            
            output = result.stdout.strip()
            # print("--------------------checkIfDeployed:", output)
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
            "node", self.interact_file_path, "isValidator", node_Signature
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
            "node", self.interact_file_path,
            "proposeValidatorVote", address, add
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output proposeValidatorVote:", output)

        return output
        
    def emitValidatorProposalToChain(self, address):
        result = subprocess.run([
            "node", self.interact_file_path,
            "emitValidatorProposalToChain", address
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output emitValidatorProposalToChain:", output)
        return output
        
    def listenForValidatorProposal(self):
        result = subprocess.run([
            "node", self.interact_file_path,
            "listenForValidatorProposal"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output listenForValidatorProposal:", output)
        return output

    def get_all_validators(self):

        result = subprocess.run([
            "node", self.interact_file_path, "getValidatorsByBlockNumber"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        # print("Raw JS Output getAllValidators:", output)

        return output
    
    def get_peers(self):
        result = subprocess.run([
            "node", self.interact_file_path, "getPeerCount"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        # print("Raw JS Output getAllValidators:", output)

        return output
    
    def issue_capability_token(self, from_node, to_node):

        result = subprocess.run([
            "node", self.interact_file_path, "issueCapabilityToken",
            from_node, to_node
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output issueCapabilityToken:", output)
        return output
    
    def revoke_capability_token(self, from_node, to_node):
        result = subprocess.run([
            "node", self.interact_file_path, "revokeCapabilityToken",
            from_node, to_node
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output revokeCapabilityToken:", output)
        return output
    
    def get_capability_token(self, from_node, to_node):
        result = subprocess.run([
            "node", self.interact_file_path, "getCapabilityToken",
            from_node, to_node
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        # print("Raw JS Output getCapabilityToken:", output)

        return output

    
    def check_token_expiry(self, from_node, to_node, validity_period):
        result = subprocess.run([
            "node", self.interact_file_path, "checkTokenExpiry",
            from_node, to_node, validity_period
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        print("Raw JS Output checkTokenExpiry:", output)
        if output == "true":
            # print("Token is valid and not expired.")
            return True
        elif output == "false":
            # print("Token is expired.")
            return False
    
    def check_token_availability(self, from_node, to_node):
        result = subprocess.run([
            "node", self.interact_file_path, "checkCapabilityToken",
            from_node, to_node
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        if output == "true":
            # print("Capability token is available.")
            return True
        elif output == "false":
            # print("Capability token is not available.")
            return False
        
        

    def setup_routes(self):
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
                    
                        # blockchain = BlockchainInit()
                        # blockchain.update_extra_data_in_genesis()

                        get_All_validators = self.get_all_validators()
                        print("All Validators:", get_All_validators)
    
                        if data["address"] in get_All_validators:
                            print("Address already exists. This is an initial validator. Thus, it already exists ...")
                            get_All_validators = self.get_all_validators()
                            print("All Validators:", get_All_validators)
                            return jsonify({"status": "success", "message": f"Node {data['address']} is already registered as a validator."}), 200

                        print(f"Sending acknowledgment to the Fog Node with ID: {data['node_id']}")

                        # print(data["node_url"])
                        # print(self.genesis_file_path)
                        # print(self.node_registry_path)
                        # print(self.besu_RPC_url)
                        # print(self.prefunded_keys_file)

                        cloud_ack_sender = AcknowledgementSender(data["node_url"], self.genesis_file_path, self.node_registry_path, self.besu_RPC_url, self.prefunded_keys_file)
                        response = jsonify({"status": "success...", "message": "Node registered successfully", "raw_output": raw_output})
                        cloud_ack_sender.send_acknowledgment(node_id="Cloud")


                        # Check if the node is a validator
                        if self.checkValidator(data["signature"]):
                            print("Node is a validator.\n")

                            get_All_validators = self.get_all_validators()
                            print("Current Available:", get_All_validators)

                            print("...  Waiting For some time for the fog node to start the blockchain to proposing it as a validator.  ")

                            # Wait for peers to increase
                            initial_peers = int(self.get_peers())
                            print("Initial Peers Count:", initial_peers)

                            while True:
                                current_peers = int(self.get_peers())
                                print("Current Peers Count:", current_peers)

                                if current_peers > initial_peers:
                                    print("Peers count has increased.")
                                    break

                                print("Waiting for peers to increase...")
                                time.sleep(5)


                            emitValidatorProposal = self.emitValidatorProposalToChain(data["address"])
                            print("Emit Validator Proposal Response:", emitValidatorProposal)

                            print("Proposing the validator...")
                            response = self.proposeValidator(data["address"], "true")
                            print("DEBUG-------> Propose Validator Response:", response)
                            
                            # listenForValidatorProposal = self.listenForValidatorProposal()
                            # print("Listening for Validator Proposal Response:", listenForValidatorProposal)
                            # # print("Validator proposal response:", response)

                            # listenForValidatorProposal = listenForValidatorProposal.split("\n")
                            # print("Listening for Validator Proposal Response:", listenForValidatorProposal)

                            get_All_validators = self.get_all_validators()
                            print("All Validators:", get_All_validators)
                            while data["address"] not in get_All_validators:
                                print("Validator is not added yet. Waiting for some time.")
                                get_All_validators = self.get_all_validators()
                                print("All Validators:", get_All_validators)
                                time.sleep(7)

                            return jsonify({"status": "success", "message": f"All validators agreed ------ Validator {data['address']} is added to the blockchain."}), 200

                            # Add the validator address to the JSON file
                            # self.add_validator_address(data["address"])
                        else:
                            print("Node is not a validator.")
                            return jsonify({"status": "success", "message": f"Registration Successful: Node {data['address']} not a Validator."}), 200
                
                    else:
                        print("Error in registering node on the blockchain:", message)
                        return jsonify({"status": "error", "message": message}), 500
            else:
                print("Signature Verification Failed. The node is invalid.")
                return jsonify({"status": "error", "message": "Signature verification failed"}), 400
                    

        @self.app.route("/read", methods=["GET"])
        def read():
            print("Received Read Request")
            from_signature = request.args.get("signature")
            node_id = request.args.get("node_id")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", from_signature)

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
            
            
            result, status_code = self.is_node_registered_js(from_signature)
            print("Node Registration Check Result:", result)

            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    to_signature = node_data.get("signature")
            else:
                print("Details of this Node not found. This needs to be registered first.")
                return jsonify({"status": "error", "message": "Details of the connected node not found."}), 404
            
            if result["registered"]:

                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("Token Availability Check:", check_token)
                if check_token:
                    print("Token is available.")
                    # Check if the node is allowed to read
                    # Check token expiry
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("Token Expiry Check:", check_expiry)
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")
                        # Issue a new token
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Token is valid and not expired.")
                        # Check permissions

                else:
                    print("Token is not available.")
                    # Issue a new token
                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    print("New Capability Token:", issue_token)


                get_token = self.get_capability_token(from_signature, to_signature)
                print("DEBUG----------->Capability Token:", get_token)
                # Extract the policy line from the capability token output
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    # Remove "Policy:" and strip whitespace
                    policy_data = policy_line.replace("Policy:", "").strip()

                    # Split by colon to separate flow and permissions
                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "READ" in permissions:
                            print(f"Allowed to read on Cloud with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id} is allowed to read"}), 200
                        else:
                            print(f"Not allowed to read on Cloud: {permissions}")
                            return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to read"}), 200
                    else:
                        print("Invalid policy format.")
                        return jsonify({"status": "error", "message": "Invalid policy format"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404


        @self.app.route("/write", methods=["POST"])
        def write():
            print("Received Read Request")
            from_signature = request.args.get("signature")


            with open("/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/node-details.json", "r") as json_file:
                node_data = json.load(json_file)
                to_signature = node_data.get("signature")

            node_id = request.args.get("node_id")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", from_signature)

            result, status_code = self.is_node_registered_js(from_signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:

                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("Token Availability Check:", check_token)
                if check_token:
                    print("Token is available.")
                    # Check if the node is allowed to read
                    # Check token expiry
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("Token Expiry Check:", check_expiry)
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")
                        # Issue a new token
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Token is valid and not expired.")
                        # Check permissions
                else:
                    print("Token is not available.")
                    # Issue a new token
                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                print("DEBUG----------->Capability Token:", get_token)
                # Extract the policy line from the capability token output
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    # Remove "Policy:" and strip whitespace
                    policy_data = policy_line.replace("Policy:", "").strip()

                    # Split by colon to separate flow and permissions
                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "WRITE" in permissions:
                            print(f"Allowed to write on Cloud with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id} is allowed to write"}), 200
                        else:
                            print(f"Not allowed to write on Cloud: {permissions}")
                            return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to write"}), 200
                    else:
                        print("Invalid policy format.")
                        return jsonify({"status": "error", "message": "Invalid policy format"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404

        @self.app.route("/execute", methods=["POST"])
        def execute():
            print("Received Read Request")
            from_signature = request.args.get("signature")


            with open("/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/node-details.json", "r") as json_file:
                node_data = json.load(json_file)
                to_signature = node_data.get("signature")

            node_id = request.args.get("node_id")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", from_signature)

            result, status_code = self.is_node_registered_js(from_signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:

                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("Token Availability Check:", check_token)
                if check_token:
                    print("Token is available.")
                    # Check if the node is allowed to read
                    # Check token expiry
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("Token Expiry Check:", check_expiry)
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")
                        # Issue a new token
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Token is valid and not expired.")
                        # Check permissions
                else:
                    print("Token is not available.")
                    # Issue a new token
                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                print("DEBUG----------->Capability Token:", get_token)
                # Extract the policy line from the capability token output
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    # Remove "Policy:" and strip whitespace
                    policy_data = policy_line.replace("Policy:", "").strip()

                    # Split by colon to separate flow and permissions
                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "EXECUTE" in permissions:
                            print(f"Allowed to execute on Cloud with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id} is allowed to execute"}), 200
                        else:
                            print(f"Not allowed to execute on Cloud: {permissions}")
                            return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to execute"}), 200
                    else:
                        print("Invalid policy format.")
                        return jsonify({"status": "error", "message": "Invalid policy format"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404

        @self.app.route("/transmit", methods=["POST"])
        def transmit():
            print("Received Read Request")
            from_signature = request.args.get("signature")


            with open("/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/node-details.json", "r") as json_file:
                node_data = json.load(json_file)
                to_signature = node_data.get("signature")

            node_id = request.args.get("node_id")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            print(f"Signature of the {node_id} :", from_signature)

            result, status_code = self.is_node_registered_js(from_signature)
            print("Node Registration Check Result:", result)

            if result["registered"]:

                print("Node is registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("Token Availability Check:", check_token)
                if check_token:
                    print("Token is available.")
                    # Check if the node is allowed to read
                    # Check token expiry
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("Token Expiry Check:", check_expiry)
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")
                        # Issue a new token
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Token is valid and not expired.")
                        # Check permissions

                else:
                    print("Token is not available.")
                    # Issue a new token
                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                print("DEBUG----------->Capability Token:", get_token)
                # Extract the policy line from the capability token output
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    # Remove "Policy:" and strip whitespace
                    policy_data = policy_line.replace("Policy:", "").strip()

                    # Split by colon to separate flow and permissions
                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "TRANSMIT" in permissions:
                            print(f"Allowed to transmit on Cloud with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id} is allowed to transmit"}), 200
                        else:
                            print(f"Not allowed to transmit on Cloud: {permissions}")
                            return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to transmit"}), 200
                    else:
                        print("Invalid policy format.")
                        return jsonify({"status": "error", "message": "Invalid policy format"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id} is not registered.  Register the node first"}), 404



    def run(self, host, port):
        """Run the Flask application."""
        self.app.run(host=host, port=port)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python node_registry.py <besu_RPC_url> <registering_node_url>")
        sys.exit(1)
    besu_RPC_url = sys.argv[1]
    print("RPC URL:", besu_RPC_url)
    port = sys.argv[2]
    print("Port:", port)
    # Initialize the NodeRegistry with the provided arguments
    registry = NodeRegistry(besu_RPC_url)
    registry.run("0.0.0.0", port)


