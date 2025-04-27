from flask import Flask, request, jsonify
from acknowledgement import AcknowledgementSender
import json
import threading
import os
import subprocess
import time
import sys
import re 
from eth_keys import keys
from eth_utils import keccak

class NodeRegistry:

    def __init__(self, besu_RPC_url):
        self.root_path = os.path.dirname(os.path.abspath(__file__))
        self.data_path = os.path.join(self.root_path, "data/")
        self.genesis_files_path = os.path.join(self.root_path, "genesis/")
        self.genesis_file_path = os.path.join(self.genesis_files_path, "genesis.json")
        self.node_registry_path = os.path.join(self.data_path, "NodeRegistry.json")
        self.prefunded_keys_file = os.path.join(self.root_path, "prefunded_keys.json")
        self.interact_file_path = os.path.join(self.root_path, "interact.js")
        self.node_details = os.path.join(self.root_path, "node-details.json")
        self.besu_RPC_url = besu_RPC_url

        listener_thread = threading.Thread(target=self.listenForValidatorProposal)
        listener_thread.daemon = True 
        listener_thread.start()
        self.app = Flask(__name__)
        self.setup_routes()                

    def verify_node_identity(self, data):
        try:
            signature_hex = data.get("signature")
            public_key_hex = data.get("public_key")

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

    def register_node_on_chain(self, node_id, node_name, node_type, public_key, address, rpcURL, receiver_node_type, signature):
        try:
            result = subprocess.run([
                "node", self.interact_file_path, "registerNode",
                node_id, node_name, node_type, public_key, address, rpcURL, receiver_node_type, signature
            ], capture_output=True, text=True)

            output = result.stdout.strip()

            if result.returncode != 0:
                return "error", result.stderr.strip(), output  
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
                details = json.loads(output)
                return {"status": "success", "details": details}, 200
            except json.JSONDecodeError:
                return {"status": "error", "message": "Invalid JSON from JS", "raw_output": output}, 500

        except Exception as e:
            return {"status": "error", "message": str(e)}, 500
    

    def check_smart_contract(self):
        if os.path.exists(self.node_registry_path):
            print("Node registry file exists. Smart contract can be checked onchain.")
            return True
        else:
            print("Node registry file does not exist. Smart contract cannot be checked onchain.")
            return False
        
    def check_smart_contract_deployment(self):
            result = subprocess.run([
                "node", self.interact_file_path, "checkIfDeployed",
            ], capture_output=True, text=True)
            
            output = result.stdout.strip()
            # print("--------------------checkIfDeployed:", output)
            if output == "true":
                return True
            elif output == "false":
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
        result = subprocess.run([
            "node", self.interact_file_path,
            "proposeValidatorVote", address, add
        ], capture_output=True, text=True)

        output = result.stdout.strip()
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
        while True:
            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    signature = node_data.get("signature")
                    node_id = node_data.get("node_id")
                    node_name = node_data.get("node_name")


                is_validator = self.checkValidator(signature)

                if is_validator:
                    result = subprocess.run(
                        ["node", self.interact_file_path, "listenForValidatorProposals"],
                        capture_output=True,
                        text=True
                    )
                    addresses = [address.lower() for address in re.findall(r'0x[a-fA-F0-9]{40}', result.stdout)]
                    all_validators = self.get_all_validators()
                    new_addresses = [addr for addr in addresses if addr not in all_validators]
                    if new_addresses:

                        print("---------------------------------")
                        print(f"This Node is a Validator.\n Proposer Details : {node_id}: {node_name}")
                        print("---------------------------------\n")
                        # print(f"Proposing Validator with address: {new_addresses}")
                        for new_validator in new_addresses:
                            print(f"Proposing Validator with address: {new_validator}")
                            response = self.proposeValidator(new_validator, "true")
                            print(f"Proposed {new_validator} → Response: {response}")
                    # else:
                    #     print("All proposed addresses are already validators.")
                else:
                    print(f"{node_id}: {node_name}is not a validator.")
                    print("Stopping the listener thread.")
                    break
            else:
                print("Waiting for the Node to get registered to start the eventListener.")
            time.sleep(10)

    def get_all_validators(self):

        result = subprocess.run([
            "node", self.interact_file_path, "getValidatorsByBlockNumber"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        return output
    
    def get_peers(self):
        result = subprocess.run([
            "node", self.interact_file_path, "getPeerCount"
        ], capture_output=True, text=True)

        output = result.stdout.strip()
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
        return output

    
    def check_token_expiry(self, from_node, to_node, validity_period):
        result = subprocess.run([
            "node", self.interact_file_path, "checkTokenExpiry",
            from_node, to_node, validity_period
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        if output == "true":
            return True
        elif output == "false":
            return False
    
    def check_token_availability(self, from_node, to_node):
        result = subprocess.run([
            "node", self.interact_file_path, "checkCapabilityToken",
            from_node, to_node
        ], capture_output=True, text=True)

        output = result.stdout.strip()
        if output == "true":
            return True
        elif output == "false":
            return False
        




    def setup_routes(self):
        """Setup Flask API routes inside the class."""

        @self.app.route("/register-node", methods=["POST"])
        def register_node():
            check_smart_contract = self.check_smart_contract()

            if check_smart_contract:
                if self.check_smart_contract_deployment():
                    print("Smart Contract correctly deployed.\n")
                    print("----------------------------------")
                    print("Received Node Registration Request")
                    print("----------------------------------\n")
                else:
                    print("Error with Smart Contract File. Redeploy or Check interact.js")
                    return jsonify({"status": "error", "message": "Older version of smart contract deployed. Update required by admin."}), 500
            else:
                print("Deploy Smart Contract first")
                return jsonify({"status": "error", "message": "Smart contract not deployed... Wait for admin to deploy Smart Contract..."}), 500
            
            data = request.json
            print("Node Details:")
            for key, value in data.items():
                print(f"{key}: {value}")
            print("---------------------")

            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    node_id = node_data.get("node_id")
                    node_type = node_data.get("node_type")
            else:
                node_id = data["node_id"]
                node_type = data["node_type"]
                    
            verify_result = self.verify_node_identity(data)
            print("\nChecking Received Node Signature...")
            if verify_result is True:
                print("Signature Verification Successful. The node is valid.")
                print("\nCheck if the node is registered on the blockchain...")
                result, status_code = self.is_node_registered_js(data["signature"])
                # print("Node Registration Check Result:", result) 
                if result["registered"] == True:
                    print("Node is already registered on the blockchain.")
                    return jsonify({"status": "error", "message": "Node already registered on the blockchain"}), 409
                else:
                    print("Node is not registered on the blockchain. Proceeding with registration.")
                    status, message, raw_output = self.register_node_on_chain(
                        data["node_id"], data["node_name"], data["node_type"], data["public_key"],
                        data["address"], data["rpcURL"], node_type, data["signature"]
                    )

                    if status == "success":
                        print("Node registered successfully on the blockchain.\n")
                        print("Node Registration Output Message:")
                        for line in raw_output.split("\n"):
                            print(line)
                    
                        get_All_validators = self.get_all_validators()
                        print("\nAll Validators:", get_All_validators)
    
                        if data["address"] in get_All_validators:
                            print("Address already exists. This is a Root chain validator")
                            get_All_validators = self.get_all_validators()
                            # print("All Validators:", get_All_validators)
                            return jsonify({"status": "success", "message": f"Node{data['node_type']} with ID: {data['node_id']} is a root chain. It is already registered as a validator."}), 200

                        print(f"Sending acknowledgment to the {data['node_type']} with ID: {data['node_id']}")
                        print("All Validators:", get_All_validators)

                        if data["node_type"] != "Sensor" or data["node_type"] != "Activator":
                            cloud_ack_sender = AcknowledgementSender(data["node_url"], self.genesis_file_path, self.node_registry_path, self.besu_RPC_url, self.prefunded_keys_file)
                            # response = jsonify({"status": "success...", "message": "Node registered successfully", "raw_output": raw_output})
                            cloud_ack_sender.send_acknowledgment(node_id)

                        if self.checkValidator(data["signature"]):
                            print(f"{data['node_id']} is a validator.\n")

                            get_All_validators = self.get_all_validators()
                            print("Current Available Validators:", get_All_validators)
                            print(f"\nWaiting for {data['node_id']}:{data['node_name']} to connect to chain network")
                            initial_peers = int(self.get_peers())
                            print("\nInitial Peers Count:", initial_peers)

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

                            get_All_validators = self.get_all_validators()
                            print("All Validators:", get_All_validators)

                            while data["address"] not in get_All_validators:
                                print("Validator is not added yet. Waiting for some time.")
                                get_All_validators = self.get_all_validators()
                                time.sleep(5)

                            print(f"Consensus Reached...\nAll validators agreed to add {data['node_id']}: {data['node_name']}  as a Validator...")
                            return jsonify({"status": "success", "message": f"Registration Successful...Consensus Reached...All validators agreed to add {data['node_id']}: {data['node_name']}  as a Validator..."}), 200

                        else:
                            print(f"{data['node_id']} is not a validator.\n")
                            return jsonify({"status": "success", "message": f"Registration Successful: Node {data['node_id']}: {data['node_name']} not a Validator."}), 200
                
                    else:
                        print("Error in registering node on the blockchain:", message)
                        return jsonify({"status": "error", "message": message}), 500
            else:
                print("Signature Verification Failed. The node is invalid.")
                return jsonify({"status": "error", "message": "Signature verification failed"}), 400
                    

        @self.app.route("/read", methods=["GET"])
        def read():
            from_signature = request.args.get("signature")
            node_id = request.args.get("node_id")
            node_name = request.args.get("node_name")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            # print(f"Signature of the {node_id} :", from_signature)

            check_smart_contract = self.check_smart_contract()

            if check_smart_contract:
                if self.check_smart_contract_deployment():
                    print("Smart Contract correctly deployed.\n")
                    print("----------------------------------")
                    print("Received Node Read Request")
                    print("----------------------------------\n")
                else:
                    print("Error with Smart Contract File. Redeploy or Check interact.js")
                    return jsonify({"status": "error", "message": "Older version of smart contract deployed. Update required by admin."}), 500
            else:
                print("Deploy Smart Contract first")
                return jsonify({"status": "error", "message": "Smart contract not deployed... \nWait for admin to deploy Smart Contract..."}), 500
            result, status_code = self.is_node_registered_js(from_signature)
            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    to_node_name = node_data.get("node_name")
                    to_node_id = node_data.get("node_id")
                    to_signature = node_data.get("signature")
            else:
                print("Details of this Node not found. Register First.")
                return jsonify({"status": "error", "message": "Details of the connected node not found."}), 404
            
            if result["registered"]:
                print("Node is already registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                # print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("\nChecking if the Token is avalilable:")
                if check_token:
                    print("Token is available.")
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("\n Checking Token Expiration:")
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")

                        revoke_token = self.revoke_capability_token(from_signature, to_signature)
                        print("Revoke Capability Token:", revoke_token)
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Not Expired. Token is valid.")

                else:
                    print("Token is not available. Issuing New Token...")

                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    time.sleep(5)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    policy_data = policy_line.replace("Policy:", "").strip()

                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "READ" in permissions:
                            print(f"{node_id}:{node_name} is allowed to read at {to_node_name}:{to_node_id} with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id}:{node_name} is allowed to read at {to_node_name}:{to_node_id}"}), 200
                        else:
                            print(f"{node_id}:{node_name} is not allowed to read at {to_node_name}:{to_node_id}")
                            return jsonify({"status": "failure", "message": f"Node {node_id}:{node_name} is not allowed to read at {to_node_name}:{to_node_id}"}), 200
                    else:
                        print("Invalid policy.")
                        print(policy_data)
                        return jsonify({"status": "error", "message": f"{policy_data}"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id}:{node_name} is not registered.  Register the node first"}), 404


        @self.app.route("/write", methods=["POST"])
        def write():
            from_signature = request.args.get("signature")
            node_id = request.args.get("node_id")
            node_name = request.args.get("node_name")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            # print(f"Signature of the {node_id} :", from_signature)

            check_smart_contract = self.check_smart_contract()

            if check_smart_contract:
                if self.check_smart_contract_deployment():
                    print("Smart Contract correctly deployed.\n")
                    print("----------------------------------")
                    print("Received Node Write Request")
                    print("----------------------------------\n")
                else:
                    print("Error with Smart Contract File. Redeploy or Check interact.js")
                    return jsonify({"status": "error", "message": "Older version of smart contract deployed. Update required by admin."}), 500
            else:
                print("Deploy Smart Contract first")
                return jsonify({"status": "error", "message": "Smart contract not deployed... \nWait for admin to deploy Smart Contract..."}), 500
            result, status_code = self.is_node_registered_js(from_signature)
            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    to_node_name = node_data.get("node_name")
                    to_node_id = node_data.get("node_id")
                    to_signature = node_data.get("signature")
            else:
                print("Details of this Node not found. Register First.")
                return jsonify({"status": "error", "message": "Details of the connected node not found."}), 404
            
            if result["registered"]:
                print("Node is already registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                # print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("\nChecking if the Token is avalilable:")
                if check_token:
                    print("Token is available.")
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("\n Checking Token Expiration:")
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")

                        revoke_token = self.revoke_capability_token(from_signature, to_signature)
                        print("Revoke Capability Token:", revoke_token)
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Not Expired. Token is valid.")

                else:
                    print("Token is not available. Issuing New Token...")

                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    time.sleep(5)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    policy_data = policy_line.replace("Policy:", "").strip()

                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "WRITE" in permissions:
                            print(f"{node_id}:{node_name} is allowed to write at {to_node_name}:{to_node_id} with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id}:{node_name} is allowed to write at {to_node_name}:{to_node_id}"}), 200
                        else:
                            print(f"{node_id}:{node_name} is not allowed to write at {to_node_name}:{to_node_id}")
                            return jsonify({"status": "failure", "message": f"Node {node_id}:{node_name} is not allowed to write at {to_node_name}:{to_node_id}"}), 200
                    else:
                        print("Invalid policy.")
                        print(policy_data)
                        return jsonify({"status": "error", "message": f"{policy_data}"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id}:{node_name} is not registered.  Register the node first"}), 404
            

        @self.app.route("/execute", methods=["POST"])
        def execute():
            from_signature = request.args.get("signature")
            node_id = request.args.get("node_id")
            node_name = request.args.get("node_name")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            # print(f"Signature of the {node_id} :", from_signature)

            check_smart_contract = self.check_smart_contract()

            if check_smart_contract:
                if self.check_smart_contract_deployment():
                    print("Smart Contract correctly deployed.\n")
                    print("----------------------------------")
                    print("Received Node Execute Request")
                    print("----------------------------------\n")
                else:
                    print("Error with Smart Contract File. Redeploy or Check interact.js")
                    return jsonify({"status": "error", "message": "Older version of smart contract deployed. Update required by admin."}), 500
            else:
                print("Deploy Smart Contract first")
                return jsonify({"status": "error", "message": "Smart contract not deployed... \nWait for admin to deploy Smart Contract..."}), 500
            result, status_code = self.is_node_registered_js(from_signature)
            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    to_node_name = node_data.get("node_name")
                    to_node_id = node_data.get("node_id")
                    to_signature = node_data.get("signature")
            else:
                print("Details of this Node not found. Register First.")
                return jsonify({"status": "error", "message": "Details of the connected node not found."}), 404
            
            if result["registered"]:
                print("Node is already registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                # print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("\nChecking if the Token is avalilable:")
                if check_token:
                    print("Token is available.")
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("\n Checking Token Expiration:")
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")

                        revoke_token = self.revoke_capability_token(from_signature, to_signature)
                        print("Revoke Capability Token:", revoke_token)
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Not Expired. Token is valid.")

                else:
                    print("Token is not available. Issuing New Token...")

                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    time.sleep(5)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    policy_data = policy_line.replace("Policy:", "").strip()

                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "EXECUTE" in permissions:
                            print(f"{node_id}:{node_name} is allowed to execute at {to_node_name}:{to_node_id} with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id}:{node_name} is allowed to execute at {to_node_name}:{to_node_id}"}), 200
                        else:
                            print(f"{node_id}:{node_name} is not allowed to execute at {to_node_name}:{to_node_id}")
                            return jsonify({"status": "failure", "message": f"Node {node_id}:{node_name} is not allowed to execute at {to_node_name}:{to_node_id}"}), 200
                    else:
                        print("Invalid policy.")
                        print(policy_data)
                        return jsonify({"status": "error", "message": f"{policy_data}"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id}:{node_name} is not registered.  Register the node first"}), 404        
           
        @self.app.route("/transmit", methods=["POST"])
        def transmit():
            from_signature = request.args.get("signature")
            node_id = request.args.get("node_id")
            node_name = request.args.get("node_name")

            if not from_signature:
                return jsonify({"status": "error", "message": "Missing signature"}), 400

            # print(f"Signature of the {node_id} :", from_signature)

            check_smart_contract = self.check_smart_contract()

            if check_smart_contract:
                if self.check_smart_contract_deployment():
                    print("Smart Contract correctly deployed.\n")
                    print("----------------------------------")
                    print("Received Node Transmit Request")
                    print("----------------------------------\n")
                else:
                    print("Error with Smart Contract File. Redeploy or Check interact.js")
                    return jsonify({"status": "error", "message": "Older version of smart contract deployed. Update required by admin."}), 500
            else:
                print("Deploy Smart Contract first")
                return jsonify({"status": "error", "message": "Smart contract not deployed... Wait for admin to deploy Smart Contract..."}), 500
            result, status_code = self.is_node_registered_js(from_signature)
            if os.path.exists(self.node_details):
                with open(self.node_details, "r") as json_file:
                    node_data = json.load(json_file)
                    to_node_name = node_data.get("node_name")
                    to_node_id = node_data.get("node_id")
                    to_signature = node_data.get("signature")
            else:
                print("Details of this Node not found. Register First.")
                return jsonify({"status": "error", "message": "Details of the connected node not found."}), 404
            
            if result["registered"]:
                print("Node is already registered on the blockchain.")
                details, status = self.get_node_details_js(from_signature)
                # print(details)

                check_token = self.check_token_availability(from_signature, to_signature)
                print("\nChecking if the Token is avalilable:")
                if check_token:
                    print("Token is available.")
                    validity_period = "360000"

                    check_expiry = self.check_token_expiry(from_signature, to_signature, validity_period)
                    print("\n Checking Token Expiration:")
                    if check_expiry:
                        print("Token is expired and needs to be renewed.")

                        revoke_token = self.revoke_capability_token(from_signature, to_signature)
                        print("Revoke Capability Token:", revoke_token)
                        issue_token = self.issue_capability_token(from_signature, to_signature)
                        print("New Capability Token:", issue_token)

                    else:
                        print("Not Expired. Token is valid.")

                else:
                    print("Token is not available. Issuing New Token...")

                    issue_token = self.issue_capability_token(from_signature, to_signature)
                    time.sleep(5)
                    print("New Capability Token:", issue_token)

                get_token = self.get_capability_token(from_signature, to_signature)
                policy_line = None
                for line in get_token.split("\n"):
                    if line.startswith("Policy:"):
                        policy_line = line
                        break

                print("Extracted Policy Line:", policy_line)
                if policy_line:
                    policy_data = policy_line.replace("Policy:", "").strip()

                    if ":" in policy_data:
                        flow, permissions_str = policy_data.split(":", 1)
                        permissions = [p.strip() for p in permissions_str.split(",")]

                        print("Flow:", flow)
                        print("Permissions:", permissions)

                        if "TRANSMIT" in permissions:
                            print(f"{node_id}:{node_name} is allowed to transmit at {to_node_name}:{to_node_id} with flow {flow}: {permissions}")
                            return jsonify({"status": "success", "message": f"Node {node_id}:{node_name} is allowed to transmit at {to_node_name}:{to_node_id}"}), 200
                        else:
                            print(f"{node_id}:{node_name} is not allowed to transmit at {to_node_name}:{to_node_id}")
                            return jsonify({"status": "failure", "message": f"Node {node_id}:{node_name} is not allowed to transmit at {to_node_name}:{to_node_id}"}), 200
                    else:
                        print("Invalid policy.")
                        print(policy_data)
                        return jsonify({"status": "error", "message": f"{policy_data}"}), 400

            else:
                print("Node is not registered on the blockchain. Go through the Registration process.")
                return jsonify({"status": "error", "message": f"Node {node_id}:{node_name} is not registered.  Register the node first"}), 404

    def run(self, host, port):
        """Run the Flask application."""
        self.app.run(host=host, port=port)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python root_node_registration.py <besu_RPC_url> <registering_node_url>")
        sys.exit(1)
    besu_RPC_url = sys.argv[1]
    print("RPC URL:", besu_RPC_url)
    port = sys.argv[2]
    print("Port:", port)
    registry = NodeRegistry(besu_RPC_url)
    registry.run("0.0.0.0", port)


