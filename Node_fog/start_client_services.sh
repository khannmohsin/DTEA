#!/bin/bash


ROOT_PATH="$(pwd)"

# Check if .env file exists
if [ -f "$ROOT_PATH/.env" ]; then
    echo ".env file found. Loading configuration from .env."

    # Load .env variables into the shell and export
    set -o allexport
    source "$ROOT_PATH/.env"
    set +o allexport

else
    echo ".env file not found. Enter network configuration..."

    # Ask user for Network Configuration
    read -p "Enter FLASK_PORT: " FLASK_PORT
    read -p "Enter BESU_PORT: " BESU_PORT
    read -p "Enter P2P_PORT: " P2P_PORT

    # Create NODE_URL and BESU_RPC_URL dynamically
    NODE_URL="http://127.0.0.1:$FLASK_PORT"
    BESU_RPC_URL="http://127.0.0.1:$BESU_PORT"

    # Save to a .env file
    cat > "$ROOT_PATH/.env" <<EOL
FLASK_PORT=$FLASK_PORT
BESU_PORT=$BESU_PORT
P2P_PORT=$P2P_PORT
NODE_URL=$NODE_URL
BESU_RPC_URL=$BESU_RPC_URL
EOL

    echo ".env file created successfully with the following contents:"
    cat "$ROOT_PATH/.env"
fi

# # Network Configuration
# FLASK_PORT=5001
# BESU_PORT=8546
# NODE_URL=http://127.0.0.1:$FLASK_PORT
# BESU_RPC_URL=http://127.0.0.1:$BESU_PORT
# P2P_PORT=30304


# Set Python Virtual Environment and Paths
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"
# Define paths to Python scripts
BLOCKCHAIN_SCRIPT="$ROOT_PATH/client_blockchain_init.py"
FLASK_SCRIPT="$ROOT_PATH/client_node_registration.py"
NODE_REGISTRATION_SCRIPT="$ROOT_PATH/client_node_reg_request.py"

# ------------------------------------------------Main Functions------------------------------------------------

start_flask() {
    echo "----------------------------------"
    echo " Starting Client Node Flask API..."
    echo "----------------------------------"
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $FLASK_SCRIPT $BESU_RPC_URL $FLASK_PORT\"" 
}


initialize_chain_client() {
    echo "-----------------------------"
    echo "Initializing chain client..."
    echo "-----------------------------"
    echo ""
    if [ -f "$ROOT_PATH/data/key.priv" ] && [ -f "$ROOT_PATH/data/key.pub" ]; then
        echo "Key files are already present."
    else
        echo "Generating keys..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" generate_keys
    fi
}

start_blockchain() {
    echo "----------------------"
    echo "Starting Blockchain..."
    echo "----------------------"
    echo ""
    if [ ! -f "$ROOT_PATH/genesis/genesis.json" ] && [ ! -f "$ROOT_PAT/data/NodeRegistry.json" ]; then
        echo "Acknowledgement not received from the connecting node. Please check the Flask script."
        exit 1
    fi
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_blockchain_node $P2P_PORT $BESU_PORT\""
}

stop_blockchain() {
    echo "----------------------"
    echo "Stopping Blockchain..."
    echo "----------------------"
    echo ""
    pkill -f "besu"
    echo "Blockchain Stopped."
}

restart_blockchain() {
    echo "----------------------"
    echo "Restarting Blockchain..."
    echo "----------------------"
    stop_blockchain
    sleep 2
    start_blockchain
}


reinitialize_chain_client() {
    echo "--------------------------------------"
    echo "Removing Previously Initialized Files"
    echo "--------------------------------------"
    echo ""

    # Remove existing files
    rm -rf "$ROOT_PATH/data"
    rm -rf "$ROOT_PATH/genesis"
    rm -rf "$ROOT_PATH/node-details.json"
    rm -rf "$ROOT_PATH/prefunded_keys.json"
    rm -rf "$ROOT_PATH/.env"

    echo ""
    echo "--------------------------------------"
    echo "Reconfiguring Network Ports After Reinitialization"
    echo "--------------------------------------"
    echo ""

    # Re-ask network configuration and recreate .env
    read -p "Enter FLASK_PORT: " FLASK_PORT
    read -p "Enter BESU_PORT: " BESU_PORT
    read -p "Enter P2P_PORT: " P2P_PORT

    NODE_URL="http://127.0.0.1:$FLASK_PORT"
    BESU_RPC_URL="http://127.0.0.1:$BESU_PORT"

    cat > "$ROOT_PATH/.env" <<EOL
FLASK_PORT=$FLASK_PORT
BESU_PORT=$BESU_PORT
P2P_PORT=$P2P_PORT
NODE_URL=$NODE_URL
BESU_RPC_URL=$BESU_RPC_URL
EOL

    echo ".env file recreated successfully with new configuration:"
    cat "$ROOT_PATH/.env"

    echo ""

    # Now continue initializing blockchain
    initialize_chain_client
    echo ""
}

# reinitialize_chain_client() {
#     echo "--------------------------------------"
#     echo "Removing Previously Initialized Files"
#     echo "--------------------------------------"
#     echo ""
#     # Remove existing key files
#     rm -rf "$ROOT_PATH/data"
#     rm -rf "$ROOT_PATH/genesis"
#     rm -rf "$ROOT_PATH/node-details.json"
#     rm -rf "$ROOT_PATH/prefunded_keys.json"
#     rm -rf "$ROOT_PATH/.env"
#     initialize_chain_client
#     echo ""
# }

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
    local rpc_url="http://127.0.0.1:$BESU_PORT"
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
    echo "Starting Data Read Request"
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
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" read "$node_id" "$node_name" "$node_type" "$read_url" "$key_path"
}

node_write(){
    echo "---------------------------"
    echo "Starting Data Write Request"
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
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" write "$node_id" "$node_name" "$node_type" "$write_url" "$key_path"
}


node_remove(){
    echo "------------------------------"
    echo "Starting Data Remove Request"
    echo "------------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh remove-data <node_id> <node_name> <node_type> <connecting_port>"
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
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" remove "$node_id" "$node_name" "$node_type" "$transmit_url" "$key_path"
}

node_update(){
    echo "-----------------------------"
    echo "Starting Data Update Request"
    echo "-----------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_client_services.sh update-data <node_id> <node_name> <node_type> <connecting_port>"
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
    $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" update "$node_id" "$node_name" "$node_type" "$execute_url" "$key_path"

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
    remove-data)
        node_remove "$2" "$3" "$4" "$5"
        ;;
    update-data)
        node_update "$2" "$3" "$4" "$5"
        ;;
    help)
        echo "Usage: $0 <operation> [args]"
        echo ""
        echo "Available operations:"
        echo "  start-flask           Start the Flask API for node registration"
        echo "  init-chain-client     Initialize the chain client (generate keys, create genesis, extra data)"
        echo "  start-chain-client    Start the chain client"
        echo "  stop-chain-client     Stop the chain client"
        echo "  restart-chain-client  Restart the chain client"
        echo "  update-validators     Listen for validator updates"
        echo "  reinit-chain-client   Reinitialize the chain client and remove keys"
        echo "  register              Register this Node"
        echo "  read-data             Read data from this Node"
        echo "  write-data            Write data from this Node"
        echo "  remove-data         Transmit data from this Node"
        echo "  update-data          Execute data from this Node"
        echo "  help                  Show this help message"
        echo ""
        ;;
    *)
        echo "Invalid operation: $1"
        echo "Use './start_client_services.sh help' for valid operations."
        exit 1
        ;;
esac
