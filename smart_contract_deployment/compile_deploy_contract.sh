#!/bin/bash

# Set variables
NETWORK="besuWallet"
CONTRACT_NAME="NodeRegistry"  # Change this to match your contract name
TRUFFLE_CONFIG="truffle-config.js"
MIGRATIONS_DIR="migrations"
DEPLOY_SCRIPT="$MIGRATIONS_DIR/2_deploy_contracts.js"

# Private key and RPC URL (Update these before running the script) of the ETH account
PRIVATE_KEY="da3a5dd6ba2e9802f11dafadea0263bebc98cc6817a6ee77ca2516fa76a489ac"  # Replace with actual private key
BESU_RPC_URL="http://127.0.0.1:8545"

echo "Starting Smart Contract Deployment on Besu..."

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

# # Step 4: Install dependencies if not installed
# if [ ! -d "node_modules" ]; then
#     echo "Installing dependencies..."
#     npm install
# fi

# Step 5: Compile the contracts
echo "Compiling smart contracts..."
npx truffle compile

# Step 6: Deploy contracts to Besu
echo "Deploying contracts to Besu Blockchain..."
npx truffle migrate --network $NETWORK

# # Step 7: Open Truffle console for interaction
# echo "Opening Truffle console..."
# npx truffle console --network $NETWORK

# echo "Deployment completed successfully!"