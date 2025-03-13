#!/bin/bash

# Define paths to Python scripts
BLOCKCHAIN_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/fog_blockchain_init.py"
FLASK_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/fog_node_registration.py"
FOG_NODE_REGISTRATION_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/fog_node_reg_request.py"

# Run the fog node registration script
/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python "$FOG_NODE_REGISTRATION_SCRIPT"

# Wait for the acknowledgement from the cloud
sleep 5

echo Waiting for the acknowledgement from the cloud...
# Open a new terminal and run flask script
osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$FLASK_SCRIPT'\""


# Open a new terminal and run blockchain script
# osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$BLOCKCHAIN_SCRIPT'\""