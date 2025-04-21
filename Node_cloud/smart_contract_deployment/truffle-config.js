const HDWalletProvider = require("@truffle/hdwallet-provider");

// Load private key and RPC URL
const privateKey = "9ca1b519ef4b79b6f2e75170ac1df8d2b04f12e5824b8d16928dc83dd1a6ad90";
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
