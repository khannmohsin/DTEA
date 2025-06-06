#!/bin/bash

ROOT_DIR="/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/smart_contract_deployment"
# Set variables
cd "$ROOT_DIR" || exit
NETWORK="besuWallet"
CONTRACT_NAME="NodeRegistry"  # Change this to match your contract name
TRUFFLE_CONFIG="$ROOT_DIR/truffle-config.js"
MIGRATIONS_DIR="$ROOT_DIR/migrations"
DEPLOY_SCRIPT="$MIGRATIONS_DIR/2_deploy_contracts.js"

rm -f "$TRUFFLE_CONFIG" "$DEPLOY_SCRIPT"  # Remove old files if they exist

# Private key and RPC URL (Update these before running the script) of the ETH account
PRIVATE_KEY="$1"  # Accept private key as an argument to the script
BESU_RPC_URL="http://127.0.0.1:8545"

echo ""
# Step 1: Generate `truffle-config.js` dynamically
echo "Generating $TRUFFLE_CONFIG..."
cat <<EOL > "$TRUFFLE_CONFIG"
const HDWalletProvider = require("@truffle/hdwallet-provider");

// Load private key and RPC URL
const privateKey = "$PRIVATE_KEY";
const besuRpcUrl = "$BESU_RPC_URL";

module.exports = {
  networks: {
    besuWallet: {
      provider: () => new HDWalletProvider(privateKey, besuRpcUrl),
      network_id: "*",  // Accept any network ID
      gas: 4700000, // Increase gas limit
      gasPrice: 0,  // Set Besu to allow 0 gas for private networks
    },
  },
  compilers: {
    solc: {
      version: "0.8.0", // Explicit Solidity version
    },
  },
};
EOL

echo "$TRUFFLE_CONFIG generated successfully!"
echo ""
# Step 2: Ensure the `migrations` directory exists
if [ ! -d "$MIGRATIONS_DIR" ]; then
    echo "Creating migrations directory..."
    mkdir -p "$MIGRATIONS_DIR"
fi

# Step 3: Generate `2_deploy_contracts.js` dynamically
echo "Generating $DEPLOY_SCRIPT..."

cat <<EOL > "$DEPLOY_SCRIPT"
const $CONTRACT_NAME = artifacts.require("$CONTRACT_NAME");

module.exports = function (deployer) {
  deployer.deploy($CONTRACT_NAME);
};
EOL

echo "$DEPLOY_SCRIPT generated successfully!"
echo ""
# # Step 4: Install dependencies if not installed
# if [ ! -d "node_modules" ]; then
#     echo "Installing dependencies..."
#     npm install
# fi

# Step 5: Compile the contracts
echo "Compiling smart contracts..."
npx truffle compile
echo ""
# Step 6: Deploy contracts to Besu
echo "Deploying contracts to Besu Blockchain..."
npx truffle migrate --network $NETWORK

# # Step 7: Open Truffle console for interaction
# echo "Opening Truffle console..."
# npx truffle console --network $NETWORK

# echo "Deployment completed successfully!"