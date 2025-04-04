from flask import Flask, request, jsonify
import json
import os
import subprocess


class NodeRegistry:
    """Class to manage the registration and retrieval of nodes (Cloud, Fog, Edge, Sensor)."""

    def __init__(self, filename="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/unregistered_nodes.json"):
        """Initialize with the JSON file storing node data and set up Flask."""
        self.filename = filename
        self.nodes = self.load_nodes()
        self.app = Flask(__name__)
        self.setup_routes()

    def is_node_registered_js(self, node_id):
        try:
            result = subprocess.run(
                ["node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "isRegistered", node_id],
                capture_output=True,
                text=True
            )

            print("Node Registration Check Result:", result.stdout.strip())

            if result.returncode != 0:
                return {"status": "error", "message": result.stderr.strip()}, 500

            output = result.stdout.strip()
            if output == "true":
                return {"status": "success", "registered": True}, 200
            elif output == "false":
                return {"status": "success", "registered": False}, 200
            else:
                return {"status": "error", "message": f"Unexpected output: {output}"}, 500

        except Exception as e:
            return {"status": "error", "message": str(e)}, 500
        
    def get_node_details_js(self, node_id):
        try:
            result = subprocess.run(
                ["node", "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/interact.js", "getNodeDetails", node_id],
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
    

    def setup_routes(self):
        """Setup Flask API routes inside the class."""

        @self.app.route("/register-node", methods=["POST"])
        def register_node():
            print("Received Node Registration Request")
            data = request.json
            print("Received Node Data:", data)

            # Validate received data
            required_keys = {"node_id", "node_name", "node_type", "public_key", "address"}
            if not required_keys.issubset(data.keys()):
                return jsonify({"status": "error", "message": "Missing required fields"}), 400

            # Register node
            response, status_code = self.register_node(
                data["node_id"], data["node_name"], data["node_type"], data["public_key"], data["address"]
            )
            self.add_validator_address(data["address"])
            return jsonify(response), status_code

        @self.app.route("/read", methods=["GET"])
        def read():
            print("Received Read Request")
            node_id = request.args.get("node_id")

            if not node_id:
                return jsonify({"status": "error", "message": "Missing node_id"}), 400

            print("Node ID Received:", node_id)

            result, status_code = self.is_node_registered_js(node_id)

            if result["registered"]:
                details, status = self.get_node_details_js(node_id)
                if status == 200:
                    print("Node Details:")
                    # print(json.dumps(details["details"], indent=4))  # Pretty print
                    receiver_token = details["details"].get("receiverCapabilityToken", "")
                    permissions = [perm.strip() for perm in receiver_token.split(",")]
                    if "Cloud:WRITE" in permissions:
                        print("Allowed to write on Cloud:", receiver_token)
                        return jsonify({"status": "success", "message": f"Node {node_id} is allowed to read"}), 200
                    else:
                        print("Not Allowed to Write on Cloud:", receiver_token)
                        return jsonify({"status": "failure", "message": f"Node {node_id} is not allowed to read"}), 200
                else:
                    print("Error fetching node details:")
                    print(json.dumps(details, indent=4))
                # print("Node Details:", response)
            else:
                return jsonify({"status": "error: register the node first", "message": f"Node {node_id} is not registered"}), 404



        @self.app.route("/write", methods=["POST"])
        def write():
            print("Received Write Request")
            data = request.json
            print("Received Data:", data)

        @self.app.route("/execute", methods=["POST"])
        def execute():
            print("Received Execute Request")
            data = request.json
            print("Received Data:", data)

        @self.app.route("/transmit", methods=["POST"])
        def transmit():
            print("Received Transmit Request")
            data = request.json
            print("Received Data:", data)



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
    # registry.is_node_registered_js("FN-001")
    registry.run()

