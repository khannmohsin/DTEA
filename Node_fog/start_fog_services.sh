#!/bin/bash

# Set Python Virtual Environment and Paths
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"

# Define paths to Python scripts
BLOCKCHAIN_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/fog_blockchain_init.py"
FLASK_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/fog_node_registration.py"
FOG_NODE_REGISTRATION_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/fog_node_reg_request.py"

# Display help message
show_help() {
    echo "Usage: ./manage_blockchain.sh <operation> [args]"
    echo ""
    echo "Available operations:"
    echo "  init-blockchain         Initialize the blockchain (generate keys, create genesis, extra data)"
    echo "  start-blockchain        Start the blockchain node"
    echo "  stop         Stop the blockchain node"
    echo "  restart      Restart the blockchain node"
    echo "  reset        Reset blockchain data and start fresh"
    echo "  help         Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./manage_blockchain.sh init"
    echo "  ./manage_blockchain.sh start"
    echo "  ./manage_blockchain.sh reset"
}

# Check if the correct number of arguments is provided
if [ "$#" -lt 1 ]; then
    show_help
    exit 1
fi


# **Function to Start Flask API (Cloud Node Registration)**
start_flask() {
    echo " Starting Cloud Node Flask API..."
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $FLASK_SCRIPT\""
}

# Generate Keys 
generate_keys() {
    echo "Generating keys
    "
    $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" generate_keys
}

receive_acknowledgement() {
    echo "Waiting for the acknowledgement from the cloud...Check the flask for if the acknowledgement is received or not"
    start_flask
    sleep 5
}

# **Function to Start Blockchain**
start_blockchain() {

    if [ ! -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/genesis/genesis.json" ] && [ ! -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/enode.txt" ] && [ ! -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/NodeRegistry.json" ]; then
        echo "Acknowledgement files not received."
        exit 1
    fi
    echo "Starting Blockchain..."
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_blockchain_node\""
    # $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_blockchain_node
}

# **Function to Stop Blockchain**
stop_blockchain() {
    echo "Stopping Blockchain..."
    pkill -f "besu"
    echo "Blockchain Stopped."
}


# Node Registration Request
node_registration_request() {
    # Check if the correct number of arguments is provided
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local cloud_url="http://127.0.0.1:5000"
    local key_path="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"

    echo "Registering Fog Node..."
    receive_acknowledgement
    sleep 5
    $PYTHON_V_ENV "$FOG_NODE_REGISTRATION_SCRIPT" register "$node_id" "$node_name" "$node_type" "$cloud_url" "$key_path"
    
}

node_read(){
    # Check if the correct number of arguments is provided
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local cloud_url="http://127.0.0.1:5000"
    local key_path="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"

    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$FOG_NODE_REGISTRATION_SCRIPT" read "$node_id" "$node_name" "$node_type" "$cloud_url" "$key_path"
}

node_write(){
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local cloud_url="http://127.0.0.1:5000"
    local key_path="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"

    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$FOG_NODE_REGISTRATION_SCRIPT" write "$node_id" "$node_name" "$node_type" "$cloud_url" "$key_path"
}


node_transmit(){
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local cloud_url="http://127.0.0.1:5000"
    local key_path="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"
    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$FOG_NODE_REGISTRATION_SCRIPT" transmit "$node_id" "$node_name" "$node_type" "$cloud_url" "$key_path"
}


node_execute(){
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local cloud_url="http://127.0.0.1:5000"
    local key_path="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/key.pub"
    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$FOG_NODE_REGISTRATION_SCRIPT" execute "$node_id" "$node_name" "$node_type" "$cloud_url" "$key_path"

}


is_node_registered() {
    local node_id="$1"

    # Capture the output from the JS script
    local result=$(node -e "
        (async () => {
            const script = require('./$JS_FILE');
            try {
                const result = await script.isNodeRegistered('$node_id');
                console.log(result);  
            } catch (error) {
                console.error('Error Checking Node Registration:', error);
                console.log('ERROR'); 
            }
        })();
    ")
    echo "$result"

}

register_fog_node() {
    local node_id="$1"
    local node_name="$2"
    local node_type="$3" 
    local public_key="$4"
    local address="$5"
    
    # Call the Node.js script and capture the output (transaction hash or error)
    local result=$(node -e "
        (async () => {
            const script = require('./interact.js');
            try {
                const txHash = await script.registerNode('$node_id', '$node_name', '$node_type', '$public_key', '$address');
                console.log(txHash);  
            } catch (error) {
                console.log('ERROR');  
            }
        })();
    ")

    # Check if registration was successful
    if [ "$result" == "ERROR" ]; then
        echo "Fog Node registration failed!"
        return 1
    else
        echo "Fog Node registered successfully! Transaction Hash: $result"
        return 0
    fi
}

send_acknowledgment() {
    $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_extra_data_in_genesis
    $PYTHON_V_ENV "$ACKNOW_SCRIPT"
}

process_unregistered_nodes() {

    # Check if the unregistered_nodes.json file exists
    UNREGISTERED_NODES_FILE="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/data/unregistered_nodes.json"

    if [ ! -f "$UNREGISTERED_NODES_FILE" ]; then
        # If the file does not exist, create it
        echo "{}" > "$UNREGISTERED_NODES_FILE"
        echo "Created unregistered_nodes.json file."
    else
        echo "unregistered_nodes.json file found. Proceeding with node registration."
    fi

    while true; do
        # Read the file and register each node
        while IFS= read -r node_id; do
            echo "Checking registration status for node_id: $node_id"
            registration_status=$(is_node_registered "$node_id")
            echo "Registration status: $registration_status"

            # Register the node if it is not already registered
            if [ "$registration_status" == "false" ]; then
                echo "Registering node_id: $node_id"
                node_details=$(jq -r --arg node_id "$node_id" '.[$node_id]' "$UNREGISTERED_NODES_FILE")
                echo "Node details: $node_details"
                
                node_name=$(jq -r '.node_name' <<< "$node_details")
                node_type=$(jq -r '.node_type' <<< "$node_details")
                public_key=$(jq -r '.public_key' <<< "$node_details")
                address=$(jq -r '.address' <<< "$node_details")
                if register_fog_node "$node_id" "$node_name" $node_type "$public_key" "$address"; then
                    registration_status=$(is_node_registered "$node_id")
                    if [ "$registration_status" == "true" ]; then
                        send_acknowledgment
                        # Remove the node from the JSON file after successful registration
                        jq "del(.\"$node_id\")" "$UNREGISTERED_NODES_FILE" > tmp.$$.json && mv tmp.$$.json "$UNREGISTERED_NODES_FILE"
                        echo "Node $node_id removed from unregistered_nodes.json"
                    fi
                fi

                sleep 2
            else 
                echo "Node already registered. Skipping..."
            fi

        done < <(jq -r 'keys[]' "$UNREGISTERED_NODES_FILE")

        echo "Press [ENTER] to exit the loop or wait for 10 seconds to check again..."
        read -t 10 -n 1 key
        if [ $? -eq 0 ]; then
            echo "Exiting loop."
            break
        fi
    done
}


# Main Loop
# ✅ **Main Execution Logic**
case "$1" in
    start-flask)
        start_flask
        ;;
    start-blockchain)
        start_blockchain
        ;;
    stop-blockchain)
        stop_fog_blockchain
        ;;
    restart-blockchain)
        restart_fog_blockchain
        ;;
    init-blockchain)
        initialize_fog_blockchain
        ;;
    register)
        node_registration_request "$2" "$3" "$4"
        ;;
    get-address)
        get_fog_address
        ;;
    register-nodes)
        process_unregistered_nodes
        ;;
    read-data)
        node_read "$2" "$3" "$4"
        ;;
    write-data)
        node_write "$2" "$3" "$4"
        ;;
    transmit-data)
        node_transmit "$2" "$3" "$4"
        ;;
    execute-data)
        node_execute "$2" "$3" "$4"
        ;;
    help)
        echo "Usage: ./manage_fog_node.sh <operation>"
        echo ""
        echo "Available operations:"
        echo "  start-flask       Start Flask API for Fog Node"
        echo "  start-blockchain  Start the blockchain node"
        echo "  stop-blockchain   Stop the blockchain node"
        echo "  restart-blockchain Restart the blockchain node"
        echo "  init-blockchain   Initialize blockchain setup"
        echo "  register          Register a Fog Node"
        echo "  get-address       Get the Fog Node Address"
        echo "  register-nodes    Process unregistered nodes"
        echo ""
        echo "Examples:"
        echo "  ./manage_fog_node.sh start-flask"
        echo "  ./manage_fog_node.sh start-blockchain"
        echo "  ./manage_fog_node.sh register FN-002 'Fog Node' Fog 'http://cloud-url' '/path/to/key.pub'"
        echo "  ./manage_fog_node.sh get-address"
        echo "  ./manage_fog_node.sh register-nodes"
        ;;
    *)
        echo "Invalid operation: $1"
        echo "Use './manage_fog_node.sh help' for valid options."
        exit 1
        ;;
esac








# Run the fog node registration script
# $PYTHON_V_ENV "$FOG_NODE_REGISTRATION_SCRIPT"

# Wait for the acknowledgement from the cloud
# sleep 5

# echo Waiting for the acknowledgement from the cloud...
# # Open a new terminal and run flask script
# osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$FLASK_SCRIPT'\""


# Open a new terminal and run blockchain script
# osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$BLOCKCHAIN_SCRIPT'\""