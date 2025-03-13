from flask import Flask, request, jsonify
import json
import os

class NodeRegistry:
    """Class to manage the registration and retrieval of nodes (Cloud, Fog, Edge, Sensor)."""

    def __init__(self, filename="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/unregistered_nodes.json"):
        """Initialize with the JSON file storing node data and set up Flask."""
        self.filename = filename
        self.nodes = self.load_nodes()
        self.app = Flask(__name__)
        self.setup_routes()

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

    def register_node(self, node_id, node_name, node_type, public_key):
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
            "public_key": public_key
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

    def setup_routes(self):
        """Setup Flask API routes inside the class."""

        @self.app.route("/register-node", methods=["POST"])
        def register_node():
            print("Received Node Registration Request")
            data = request.json
            print("Received Node Data:", data)

            # Validate received data
            required_keys = {"node_id", "node_name", "node_type", "public_key"}
            if not required_keys.issubset(data.keys()):
                return jsonify({"status": "error", "message": "Missing required fields"}), 400

            # Register node
            response, status_code = self.register_node(
                data["node_id"], data["node_name"], data["node_type"], data["public_key"]
            )
            return jsonify(response), status_code
        
        @self.app.route("/acknowledgement", methods=["POST"])
        def acknowledgement():
            """Handles acknowledgment from Cloud Node, saves received files, and stores enode."""

            # Define save paths
            GENESIS_SAVE_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/genesis/genesis.json"
            NODE_REGISTRY_SAVE_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/NodeRegistry.json"
            ENODE_SAVE_PATH = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/enode.txt"

            # Ensure directories exist
            os.makedirs(os.path.dirname(GENESIS_SAVE_PATH), exist_ok=True)
            os.makedirs(os.path.dirname(NODE_REGISTRY_SAVE_PATH), exist_ok=True)
            os.makedirs(os.path.dirname(ENODE_SAVE_PATH), exist_ok=True)

            print("🔹 Received Acknowledgment Request from Cloud Node")

            # Extract metadata
            node_id = request.form.get("node_id")
            enode = request.form.get("enode")  # New: Get enode

            if not node_id:
                return jsonify({"status": "error", "message": "Missing node_id"}), 400

            # Save Genesis File
            if "genesis_file" in request.files:
                file = request.files["genesis_file"]
                file.save(GENESIS_SAVE_PATH)
                print(f"✅ Saved Genesis File to {GENESIS_SAVE_PATH}")
            else:
                print("⚠️ No Genesis File received!")

            # Save Node Registry File
            if "node_registry_file" in request.files:
                file = request.files["node_registry_file"]
                file.save(NODE_REGISTRY_SAVE_PATH)
                print(f"✅ Saved Node Registry File to {NODE_REGISTRY_SAVE_PATH}")
            else:
                print("⚠️ No Node Registry File received!")

            # Save Enode ID
            if enode:
                with open(ENODE_SAVE_PATH, "w") as enode_file:
                    enode_file.write(enode)
                print(f"✅ Enode Saved to {ENODE_SAVE_PATH}")
            else:
                print("⚠️ No Enode received!")

            print(f"✅ Acknowledgment Processed for Node {node_id}") 
            return jsonify({"status": "success", "message": "Acknowledgment received, files saved, and enode stored"}), 200
        
        
        @self.app.route("/get-nodes", methods=["GET"])
        def get_nodes():
            """Retrieve all registered nodes or filter by type."""
            node_type = request.args.get("node_type")
            nodes = self.get_nodes(node_type)
            return jsonify(nodes), 200

    def run(self, host="0.0.0.0", port=5001):
        """Run the Flask application."""
        self.app.run(host=host, port=port)

if __name__ == "__main__":
    registry = NodeRegistry()
    registry.run()

