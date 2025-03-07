const HDWalletProvider = require("@truffle/hdwallet-provider");

// Load private key and RPC URL
const privateKey = "a803de60f3b8de4acdc6abb5bc080f2cd4168cb633e4a38019b27c64c3d3439d";
const besuRpcUrl = "http://127.0.0.1:8545";

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
