#!/bin/bash

# Define paths to Python scripts
BLOCKCHAIN_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_blockchain_init.py"
FLASK_SCRIPT="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/cloud_node_registration.py"

# Open a new terminal and run Blockchain script
osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$BLOCKCHAIN_SCRIPT'\""

# Sleep to ensure Blockchain starts before Flask API
sleep 2

# Open another terminal and run Flask API script
osascript -e "tell application \"Terminal\" to do script \"/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/.venv/bin/python '$FLASK_SCRIPT'\""

echo "OnChain (Blockchain) and OffChain (Flask API) started in separate terminal windows!"