#!/bin/bash

# Set Python Virtual Environment and Paths
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"


# Network Configuration
FLASK_PORT=5000
BESU_PORT=8545
NODE_URL=http://127.0.0.1:$FLASK_PORT
BESU_RPC_URL=http://127.0.0.1:$BESU_PORT

# Define paths to Python scripts
ROOT_PATH="$(pwd)"
BLOCKCHAIN_SCRIPT="$ROOT_PATH/root_blockchain_init.py"
FLASK_SCRIPT="$ROOT_PATH/root_node_registration.py"
NODE_REGISTRATION_SCRIPT="$ROOT_PATH/root_node_reg_request.py"

# Define source and destination paths
SOURCE_FILE="$ROOT_PATH/smart_contract_deployment/build/contracts/NodeRegistry.json"
DESTINATION_DIR="$ROOT_PATH/data"

# ------------------------------------------------Main Functions------------------------------------------------

start_flask() {
    echo "---------------------------------"
    echo " Starting Root Node Flask API..."
    echo "----------------------------------"
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $FLASK_SCRIPT $BESU_RPC_URL $FLASK_PORT\"" 
}

initialize_blockchain() {
    echo "-------------------------------"
    echo "Initializing Blockchain Root..."
    echo "-------------------------------"
    echo ""
    if [ -f "$ROOT_PATH/qbftConfigFile.json" ]; then
        echo "qbftConfigFile.json is already present."
    else
        echo "Creating qbftConfigFile..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" create_qbft_file 3 1
    fi
    echo ""
    if [ -f "$ROOT_PATH/data/key.priv" ] && [ -f "$ROOT_PATH/data/key.pub" ]; then
        echo "Key files are already present."
    else
        echo "Generating keys..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" generate_keys
    fi
    echo ""
    if [ -f "$ROOT_PATH/genesis/genesis.json" ]; then
        echo "genesis.json is already present."
    else
        echo "Generating genesis file..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" create_genesis_file "$ROOT_PATH/qbftConfigFile.json"
    fi
    echo ""
    if [ -f "$ROOT_PATH/genesis/validator_address.json" ]; then
        echo "validator_address.json is already present."
    else
        echo "Extracting the validator address..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_genesis_file
    fi
    echo ""
    if [ -f "$ROOT_PATH/genesis/extraData.rlp" ]; then
        echo "extraData.rlp is already present."
    else
        echo "Updating extra data in genesis file..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_extra_data_in_genesis
    fi
}

start_blockchain() {
    echo "----------------------"
    echo "Starting Blockchain..."
    echo "----------------------"
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" start_blockchain_node\""
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

reinitialize_blockchain() {
    echo "--------------------------------------"
    echo "Removing Previously Initialized Files"
    echo "--------------------------------------"
    echo ""
    rm -rf "$ROOT_PATH/data"
    rm -rf "$ROOT_PATH/genesis"
    rm -rf "$ROOT_PATH/qbftConfigFile.json"
    rm -rf "$ROOT_PATH/prefunded_keys.json"
    rm -rf "$ROOT_PATH/node-details.json"
    initialize_blockchain
}

self_register(){
    echo "-------------------------------"
    echo "Starting Root Node Registration"
    echo "-------------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_root_services register <node_id> <node_name> <node_type>"
        exit 1
    fi
    # Extract arguments
    local node_id="$1"
    echo "-> Node ID: $node_id"
    local node_name="$2"
    echo "-> Node Name: $node_name"
    local node_type="$3"
    echo "-> Node Type: $node_type"
    local root_url="http://127.0.0.1:$FLASK_PORT"
    echo "-> Root Node URL: $root_url"
    local rpc_url="http://127.0.0.1:$BESU_PORT"
    echo "-> rpl URL: $rpc_url"
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
        $PYTHON_V_ENV "$NODE_REGISTRATION_SCRIPT" register "$node_id" "$node_name" "$node_type" "$root_url" "$key_path" "$NODE_URL" "$rpc_url"
    fi
}

node_read(){
    echo "--------------------------"
    echo "Starting Data Read Request"
    echo "--------------------------"
    echo ""
    if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ]; then
        echo "Error: Missing arguments for node registration."
        echo "Usage: ./start_fog_services.sh register <node_id> <node_name> <node_type> <port>"
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
        echo "Usage: ./start_root_services.sh write-data <node_id> <node_name> <node_type> <connecting_port>"
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
        echo "Usage: ./start_root_services.sh remove-data <node_id> <node_name> <node_type> <connecting_port>"
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
        echo "Usage: ./start_root_services.sh update-data <node_id> <node_name> <node_type> <connecting_port>"
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

smart_contract_deployment() {
    echo "------------------------------------------"
    echo "Starting Smart Contract Deployment Request"
    echo "------------------------------------------"
    echo ""
    echo "Insert the private key of the account to deploy the smart contract:"
    read -r private_key
    if [ -z "$private_key" ]; then
        echo "Private key is required to deploy the smart contract."
        exit 1
    fi
    bash $ROOT_PATH/smart_contract_deployment/compile_deploy_contract.sh $private_key
    if [ -f "$SOURCE_FILE" ]; then
        echo "Deploying smart contract..."
        cp "$SOURCE_FILE" "$DESTINATION_DIR"
        echo ""
        echo "Smart contract deployed successfully to $DESTINATION_DIR"
    else
        echo "Smart contract file not found. Please deploy the smart contract first."
    fi
}

if [ "$#" -lt 1 ]; then
    echo "-----------------------------"
    echo "Error: No operation specified"
    echo "-----------------------------"
    echo ""
    exit 1
fi

case "$1" in
    start-flask)
        start_flask
        ;;
    init-chain-root)
        initialize_blockchain
        ;;
    start-chain-root)
        start_blockchain
        ;;
    stop-chain-root)
        stop_blockchain
        ;;
    restart-chain-root)
        restart_blockchain
        ;;
    reinit-chain-root)
        reinitialize_blockchain
        ;;
    self-register)
        self_register "$2" "$3" "$4"
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
    admin)
        smart_contract_deployment
        ;;
    help)
        echo "Usage: $0 <operation> [args]"
        echo "Operations:"
        echo "  start-flask             Start the Flask API"
        echo "  init-chain-root         Initialize the blockchain root"
        echo "  start-chain-root        Start the blockchain root"
        echo "  stop-chain-root         Stop the blockchain root"
        echo "  restart-chain-root      Restart the blockchain root"
        echo "  update-validators       Listen for validator updates"
        echo "  reinit-chain-root       Reinitialize the blockchain root"
        echo "  self-register           Self-register a node"
        echo "  read-data               Read data from a node"
        echo "  write-data              Write data to a node"
        echo "  remove-data           Transmit data to a node"
        echo "  update-data            Execute data on a node"
        echo "  admin                   Deploy the smart contract"
        echo "  help                    Show this help message"
        echo ""
        ;;
    *)
        echo "Invalid operation: $1"
        echo "Use './start_root_services.sh help' for valid options."
        exit 1
        ;;
esac