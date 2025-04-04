#!/bin/bash

# Set Python Virtual Environment and Paths
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"
JS_FILE="interact.js"

# Define paths to Python scripts
BLOCKCHAIN_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_blockchain_init.py"
FLASK_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_node_registration.py"
ACKNOW_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_acknowledgement.py"

# Define source and destination paths
SOURCE_FILE="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/smart_contract_deployment/build/contracts/NodeRegistry.json"
DESTINATION_DIR="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data"

# Define JSON Files
UNREGISTERED_NODES_FILE="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/unregistered_nodes.json"


# **Function to Start Flask API (Cloud Node Registration)**
start_flask() {
    echo " Starting Cloud Node Flask API..."
    osascript -e "tell application \"Terminal\" to do script \"$PYTHON_V_ENV $FLASK_SCRIPT\""
}

# **Function to Start Blockchain**
start_blockchain() {
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

# **Function to Restart Blockchain**
restart_blockchain() {
    stop_blockchain
    sleep 2
    start_blockchain
}

initialize_blockchain() {
    # Check if qbftConfigFile.json is present
    if [ -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/qbftConfigFile.json" ]; then
        echo "qbftConfigFile.json is already present."
    else
        echo "qbftConfigFile.json is missing. Creating qbftConfigFile..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" create_qbft_file 3 1
    fi

    if [ -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/key.priv" ] && [ -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/key.pub" ]; then
        echo "Key files are already present."
    else
        echo "Key files are missing. Generating keys..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" generate_keys
    fi

    # Check if genesis.json is present
    if [ -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/genesis.json" ]; then
        echo "genesis.json is already present."
    else
        echo "genesis.json is missing. Generating genesis file..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" create_genesis_file "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/qbftConfigFile.json"
    fi

    # Check if validator_address.json is present
    if [ -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/validator_address.json" ]; then
        echo "validator_address.json is already present."
    else
        echo "validator_address.json is missing. Generating keys..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_genesis_file
    fi

    # Check if extraData.rlp is present
    if [ -f "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis/extraData.rlp" ]; then
        echo "extraData.rlp is already present."
    else
        echo "extraData.rlp is missing. Updating extra data in genesis..."
        $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_extra_data_in_genesis
    fi

    # Define source and destination paths
    SOURCE_FILE="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/smart_contract_deployment/build/contracts/NodeRegistry.json"
    DESTINATION_DIR="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data"

    # Check if the source file already exists at the destination
    if [ -f "$DESTINATION_DIR/$(basename "$SOURCE_FILE")" ]; then
        echo "Node Registry File already exists at the destination."
    else
        # Check if the source file exists
        if [ -f "$SOURCE_FILE" ]; then
            # Move the file to the destination directory
            cp "$SOURCE_FILE" "$DESTINATION_DIR"
            echo "File moved to $DESTINATION_DIR"
        else
            echo "Deploy smart contract to obtain this file."
        fi
    fi
}

reinitialize_blockchain() {
    # Remove the data directory
    rm -rf "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data"
    rm -rf "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/genesis"
    rm -rf "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/qbftConfigFile.json"
    rm -rf "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/prefunded_keys.json"

    # Reinitialize the blockchain
    initialize_blockchain
}

send_acknowledgment() {
    $PYTHON_V_ENV "$BLOCKCHAIN_SCRIPT" update_extra_data_in_genesis
    $PYTHON_V_ENV "$ACKNOW_SCRIPT"
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
    local registeredby_node_type="Cloud"

    
    # Call the Node.js script and capture the output (transaction hash or error)
    local result=$(node -e "
        (async () => {
            const script = require('./interact.js');
            try {
                const txHash = await script.registerNode('$node_id', '$node_name', '$node_type', '$public_key', '$address', '$registeredby_node_type');
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

process_unregistered_nodes() {

    # Check if the unregistered_nodes.json file exists
    UNREGISTERED_NODES_FILE="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/unregistered_nodes.json"

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


                if register_fog_node "$node_id" "$node_name" "$node_type" "$public_key" "$address"; then
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



# Check if the user provided an operation
if [ "$#" -lt 1 ]; then
    echo "Error: No operation specified"
    show_help
    exit 1
fi


# **Main Execution Logic**
case "$1" in
    start-flask)
        start_flask
        ;;
    start-blockchain)
        start_blockchain
        ;;
    stop-blockchain)
        stop_blockchain
        ;;
    restart-blockchain)
        restart_blockchain
        ;;
    init-blockchain)
        initialize_blockchain
        ;;
    register-nodes)
        process_unregistered_nodes
        ;;
    send-ack)
        send_acknowledgment
        ;;
    reinit-blockchain)
        reinitialize_blockchain
        ;;
    help)
        echo "Usage: ./manage_cloud_node.sh <operation>"
        echo ""
        echo "Available operations:"
        echo "  start-flask       Start Flask API for Cloud Node"
        echo "  start-blockchain  Start the blockchain node"
        echo "  stop-blockchain   Stop the blockchain node"
        echo "  restart-blockchain Restart the blockchain node"
        echo "  init-blockchain   Initialize blockchain setup"
        echo "  reinit-blockchain Reinitialize blockchain data and start fresh"
        echo "  register-nodes    Process unregistered nodes"
        echo "  send-ack          Send acknowledgment to Fog Node"
        echo ""
        echo "Examples:"
        echo "  ./manage_cloud_node.sh start-flask"
        echo "  ./manage_cloud_node.sh start-blockchain"
        echo "  ./manage_cloud_node.sh register-nodes"
        ;;
    *)
        echo "Invalid operation: $1"
        echo "Use './manage_cloud_node.sh help' for valid options."
        exit 1
        ;;
esac




































# while true; do
#     # Read the file and register each node
#     while IFS= read -r node_id; do
#         echo "Checking registration status for node_id: $node_id"
#         registration_status=$(is_node_registered "$node_id")
#         echo "Registration status: $registration_status"

#         # Register the node if it is not already registered
#         if [ "$registration_status" == "false" ]; then
#             echo "Registering node_id: $node_id"
#             node_details=$(jq -r --arg node_id "$node_id" '.[$node_id]' "$UNREGISTERED_NODES_FILE")
#             echo "Node details: $node_details"
            
#             node_name=$(jq -r '.node_name' <<< "$node_details")
#             node_type=$(jq -r '.node_type' <<< "$node_details")
#             public_key=$(jq -r '.public_key' <<< "$node_details")
#             address=$(jq -r '.address' <<< "$node_details")
#             if register_fog_node "$node_id" "$node_name" $node_type "$public_key" "$address"; then
#                 registration_status=$(is_node_registered "$node_id")
#                 if [ "$registration_status" == "true" ]; then
#                     /Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python "$ACKNOW_SCRIPT"
#                     # Remove the node from the JSON file after successful registration
#                     jq "del(.\"$node_id\")" "$UNREGISTERED_NODES_FILE" > tmp.$$.json && mv tmp.$$.json "$UNREGISTERED_NODES_FILE"
#                     echo "Node $node_id removed from unregistered_nodes.json"
#                 fi
#             fi

#             sleep 2
#         else 
#             echo "Node already registered. Skipping..."
#         fi

#     done < <(jq -r 'keys[]' "$UNREGISTERED_NODES_FILE")

#     # Wait for 10 seconds before checking again
#     sleep 10
# done


