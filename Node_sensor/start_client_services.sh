#!/bin/bash

# Set Python Virtual Environment and Paths
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"


# Define paths to Python scripts
ROOT_PATH="$(pwd)"
KEY_GENERATION_PATH="$ROOT_PATH/end_node_initialization.py"
NODE_REGISTRATION_SCRIPT="$ROOT_PATH/client_node_reg_request.py"

# ------------------------------------------------Main Functions------------------------------------------------


initialize_chain_client() {
    echo "-----------------------------"
    echo "Initializing chain client..."
    echo "-----------------------------"
    echo ""
    if [ -f "$ROOT_PATH/data/key.priv" ] && [ -f "$ROOT_PATH/data/key.pub" ]; then
        echo "Key files are already present."
    else
        echo "Generating keys..."
        $PYTHON_V_ENV "$KEY_GENERATION_PATH" generate_keys
    fi
}

reinitialize_chain_client() {
    echo "--------------------------------------"
    echo "Removing Previously Initialized Files"
    echo "--------------------------------------"
    echo ""
    # Remove existing key files
    rm -rf "$ROOT_PATH/data"
    rm -rf "$ROOT_PATH/node-details.json"
    initialize_chain_client
    echo ""
}

node_registration_request() {
    echo "----------------------------------"
    echo "Starting Node Registration Request"
    echo "----------------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4"]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh register <node_id> <node_name> <node_type> <connecting_port>"
        exit 1
    fi
    local node_id="$1"
    echo "-> Node ID: $node_id"
    local node_name="$2"
    echo "-> Node Name: $node_name"
    local node_type="$3"
    echo "-> Node Type: $node_type"
    local registration_url="http://127.0.0.1:$4"
    echo "-> Connecting Node URL: $registration_url"
    local rpc_url="None"
    echo "-> rpc URL: $rpc_url"
    local key_path="/$ROOT_PATH/data/key.pub"
    echo "-> Key Path: $key_path"
    echo ""
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

    if [ ! -f "$key_path" ]; then
        echo "Key file not found. Initialize blockchain first."
    else
        echo "Key file found. Continuing with registration..."
        $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" register "$node_id" "$node_name" "$node_type" "$registration_url" "$key_path" "$NODE_URL" "$rpc_url"
    fi
}

node_read(){
    echo "--------------------------"
    echo "Starting Node Read Request"
    echo "--------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh read-data <node_id> <node_name> <node_type> <connecting_port>"
        exit 1
    fi
    local node_id="$1"
    echo "-> Node ID: $node_id"
    local node_name="$2"
    echo "-> Node Name: $node_name"
    local node_type="$3"
    echo "-> Node Type: $node_type"
    local read_url="http://127.0.0.1:$4"
    echo "-> Connecting Node URL: $read_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "-> Key Path: $key_path"
    echo ""
    echo "Reading data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" read "$node_id" "$node_name" "$node_type" "$read_url" "$key_path"
}

node_write(){
    echo "---------------------------"
    echo "Starting Node Write Request"
    echo "---------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh write-data <node_id> <node_name> <node_type> <connecting_port>"
        exit 1
    fi
    local node_id="$1"
    echo "-> Node ID: $node_id"
    local node_name="$2"
    echo "-> Node Name: $node_name"
    local node_type="$3"
    echo "-> Node Type: $node_type"
    local write_url="http://127.0.0.1:$4"
    echo "-> Connecting Node URL: $write_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "-> Key Path: $key_path"
    echo ""
    echo "Writing data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" write "$node_id" "$node_name" "$node_type" "$write_url" "$key_path"
}


node_transmit(){
    echo "------------------------------"
    echo "Starting Node Transmit Request"
    echo "------------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh transmit-data <node_id> <node_name> <node_type> <connecting_port>"
        exit 1
    fi
    local node_id="$1"
    echo "-> Node ID: $node_id"
    local node_name="$2"
    echo "-> Node Name: $node_name"
    local node_type="$3"
    echo "-> Node Type: $node_type"
    local transmit_url="http://127.0.0.1:$4"
    echo "-> Connecting Node URL: $transmit_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "Key Path: $key_path"
    echo ""
    echo "Transmitting data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" transmit "$node_id" "$node_name" "$node_type" "$transmit_url" "$key_path"
}

node_execute(){
    echo "-----------------------------"
    echo "Starting Node Execute Request"
    echo "-----------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh execute-data <node_id> <node_name> <node_type> <connecting_port>"
        exit 1
    fi
    local node_id="$1"
    echo "-> Node ID: $node_id"
    local node_name="$2"
    echo "-> Node Name: $node_name"
    local node_type="$3"
    echo "-> Node Type: $node_type"
    local execute_url="http://127.0.0.1:$4"
    echo "-> Connecting Node URL: $execute_url"
    local key_path="$ROOT_PATH/data/key.pub"
    echo "-> Key Path: $key_path"
    echo ""
    echo "Executing data from the accessed node..."
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" execute "$node_id" "$node_name" "$node_type" "$execute_url" "$key_path"

}


if [ "$#" -lt 1 ]; then
    echo "-----------------------------"
    echo "Error: No operation specified"
    echo "-----------------------------"
    echo ""
    echo "Usage: $0 help"
    exit 1
fi

case "$1" in
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
        echo "  init-chain-client     Initialize the chain client (generate keys, create genesis, extra data)"
        echo "  reinit-chain-client   Reinitialize the chain client and remove keys"
        echo "  register              Register this Node"
        echo "  read-data             Read data from this Node"
        echo "  write-data            Write data from this Node"
        echo "  transmit-data         Transmit data from this Node"
        echo "  execute-data          Execute data from this Node"
        echo "  help                  Show this help message"
        echo ""
        ;;
    *)
        echo "Invalid operation: $1"
        echo "Use './start_client_services.sh help' for valid operations."
        exit 1
        ;;
esac
