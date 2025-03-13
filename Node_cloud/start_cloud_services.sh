#!/bin/bash
PYTHON_V_ENV="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python"
JS_FILE="interact.js"

# Define paths to Python scripts
BLOCKCHAIN_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_blockchain_init.py"
FLASK_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_node_registration.py"
ACKNOW_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_acknowledgement.py"


# Run the cloud node registration script
$PYTHON_V_ENV "$FLASK_SCRIPT" 
# Open a new terminal and run Blockchain script
# osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$BLOCKCHAIN_SCRIPT'\""

# Sleep to ensure Blockchain starts before Flask API
# sleep 2

# Open another terminal and run Flask API script
# osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$FLASK_SCRIPT'\""

# echo "OnChain (Blockchain) and OffChain (Flask API) started in separate terminal windows!"


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
        echo "Source file does not exist."
    fi
fi


# Check if the unregistered_nodes.json file exists
UNREGISTERED_NODES_FILE="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/unregistered_nodes.json"

if [ ! -f "$UNREGISTERED_NODES_FILE" ]; then
    # If the file does not exist, create it
    echo "{}" > "$UNREGISTERED_NODES_FILE"
    echo "Created unregistered_nodes.json file."
else
    echo "unregistered_nodes.json file found. Proceeding with node registration."
fi

# Function to check if a node is registered
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
                    /Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python "$ACKNOW_SCRIPT"
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

    # Wait for 10 seconds before checking again
    sleep 10
done


