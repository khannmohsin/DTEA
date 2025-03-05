const HDWalletProvider = require("@truffle/hdwallet-provider");

const privateKey = "1dd0945a26c326f8c2d804c47a5a1b7cdbd12d173feabb595bfe90d916c2c75d";  // ⚠️ Replace with a real private key
const besuRpcUrl = "http://127.0.0.1:8545"; // ⚠️ Replace with your Besu node's JSON-RPC URL

module.exports = {
  networks: {
    besuWallet: {
      provider: () => new HDWalletProvider(privateKey, besuRpcUrl),
      network_id: "*",  // Accept any network ID
      gas: 4700000, // Increase gas limit
      gasPrice: 0,  // Set Besu to allow 0 gas for private networks
    },
  },
};