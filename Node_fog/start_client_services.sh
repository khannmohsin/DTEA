#!/bin/bash

# Set Python Virtual Environment and Paths
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"


# Network Configuration
FLASK_PORT=5001
BESU_PORT=8546
NODE_URL=http://127.0.0.1:$FLASK_PORT
BESU_RPC_URL=http://127.0.0.1:$BESU_PORT
P2P_PORT=30303


# Define paths to Python scripts
ROOT_PATH="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog"
BLOCKCHAIN_SCRIPT="$ROOT_PATH/client_blockchain_init.py"
FLASK_SCRIPT="$ROOT_PATH/client_node_registration.py"
NODE_REGISTRATION_SCRIPT="$ROOT_PATH/client_node_reg_request.py"

# ------------------------------------------------Main Functions------------------------------------------------

# **Function to Start Flask API (Cloud Node Registration)**
start_flask() {
    echo " Starting Cloud Node Flask API..."
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $FLASK_SCRIPT $BESU_RPC_URL\""
}

initialize_chain_client() {
    echo "Initializing chain client..."

    if [ -f "$ROOT_PATH/data/key.priv" ] && [ -f "$ROOT_PATH/data/key.pub" ]; then
        echo "Key files are already present."
    else
        echo "Generating keys..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" generate_keys
    fi
}

# **Function to Start Blockchain**
start_blockchain() {
    
    if [ ! -f "$ROOT_PATH/genesis/genesis.json" ] && [ ! -f "$ROOT_PAT/data/NodeRegistry.json" ]; then
        echo "Acknowledgement not received from the cloud. Please check the Flask script."
        exit 1
    fi
    echo "Starting Blockchain..."
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_blockchain_node $P2P_PORT $BESU_PORT\""
    # $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_blockchain_node
}

# **Function to Stop Blockchain**
stop_blockchain() {
    echo "Stopping Blockchain..."
    pkill -f "besu"
    echo "Blockchain Stopped."
}


# **Function to Restart Blockchain**
restart_blockchain() {
    stop_blockchain
    sleep 2
    start_blockchain
}

listen_for_validator_updates(){
    $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_validator_event_listener
}

reinitialize_chain_client() {

    echo "Reinitializing chain client and removing keys..."
    # Remove existing key files
    rm -rf "$ROOT_PATH/data"
    rm -rf "$ROOT_PATH/genesis"
    initialize_chain_client
}

# Node Registration Request
node_registration_request() {
    # Check if the correct number of arguments is provided
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type> <port>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local registration_url="http://127.0.0.1:$4"
    echo "Registration URL: $registration_url"
    local key_path="/$ROOT_PATH/data/key.pub"
    echo "Key Path: $key_path"

    # Check if Flask is running
    if nc -z localhost "$FLASK_PORT"; then
        echo "Flask is already running on port $FLASK_PORT."
    else
        echo "Flask is not running. Starting Flask..."
        start_flask
        sleep 5
        if nc -z localhost "$FLASK_PORT"; then
            echo "Flask started successfully."
        else
            echo "Failed to start Flask. Please check the Flask script."
            exit 1
        fi
    fi
    echo ""

    # Check for existing keys
    if [ ! -f "$key_path" ]; then
        echo "Key file not found. Initialize blockchain first."
    else
        echo "Key file found. Continuing with registration..."
        $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" register "$node_id" "$node_name" "$node_type" "$registration_url" "$key_path" "$NODE_URL"
    fi
}

node_read(){
    # Check if the correct number of arguments is provided
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type> <port>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local read_url="http://127.0.0.1:$4"
    echo "Read URL: $read_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "Key Path: $key_path"

    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" read "$node_id" "$node_name" "$node_type" "$read_url" "$key_path"
}

node_write(){
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type> <port>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local write_url="http://127.0.0.1:$4"
    echo "Write URL: $write_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "Key Path: $key_path"

    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" write "$node_id" "$node_name" "$node_type" "$write_url" "$key_path"
}


node_transmit(){
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type> <port>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local transmit_url="http://127.0.0.1:$4"
    echo "Transmit URL: $transmit_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "Key Path: $key_path"


    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" transmit "$node_id" "$node_name" "$node_type" "$transmit_url" "$key_path"
}

node_execute(){
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type> <port>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "Node ID: $node_id"
    local node_name="$2"
    echo "Node Name: $node_name"
    local node_type="$3"
    echo "Node Type: $node_type"
    local execute_url="http://127.0.0.1:$4"
    echo "Execute URL: $execute_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "Key Path: $key_path"


    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" execute "$node_id" "$node_name" "$node_type" "$execute_url" "$key_path"

}


# Check if the user provided an operation
if [ "$#" -lt 1 ]; then
    echo "Error: No operation specified"
    show_help
    exit 1
fi


# Main Loop
# **Main Execution Logic**
case "$1" in
    start-flask)
        start_flask
        ;;
    init-chain-client)
        initialize_chain_client
        ;;
    start-chain-client)
        start_blockchain
        ;;
    stop-chain-client)
        stop_blockchain
        ;;
    restart-chain-client)
        restart_blockchain
        ;;
    update-validators)
        listen_for_validator_updates
        ;;
    reinit-chain-client)
        reinitialize_chain_client
        ;;
    register)
        node_registration_request "$2" "$3" "$4" "$5"
        ;;
    read-data)
        node_read "$2" "$3" "$4" "$5"
        ;;
    write-data)
        node_write "$2" "$3" "$4" "$5"
        ;;
    transmit-data)
        node_transmit "$2" "$3" "$4" "$5"
        ;;
    execute-data)
        node_execute "$2" "$3" "$4" "$5"
        ;;
    help)
        echo "Usage: $0 <operation> [args]"
        echo ""
        echo "Available operations:"
        echo "  start-flask         Start the Flask API for cloud node registration"
        echo "  init-chain-client   Initialize the chain client (generate keys, create genesis, extra data)"
        echo "  start-chain-client  Start the chain client"
        echo "  stop-chain-client   Stop the chain client"
        echo "  restart-chain-client Restart the chain client"
        echo "  update-validators   Listen for validator updates"
        echo "  reinit-chain-client Reinitialize the chain client and remove keys"
        echo "  register            Register this Node"
        echo "  read-data          Read data from this Node"
        echo "  write-data         Write data from this Node"
        echo "  transmit-data      Transmit data from this Node"
        echo "  execute-data       Execute data from this Node"
        echo "  help               Show this help message"
        echo ""
        echo "Examples:"
        echo "  ./start_client_services.sh start-flask"
        echo "  ./start_client_services.sh init-chain-client"
        echo "  ./start_client_services.sh start-chain-client"
        ;;
    *)
        echo "Invalid operation: $1"
        echo "Use 'help' to see available operations."
        exit 1
        ;;
esac
