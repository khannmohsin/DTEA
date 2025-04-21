const HDWalletProvider = require("@truffle/hdwallet-provider");

// Load private key and RPC URL
const privateKey = "68466091890f79c099e2d0de2b7e4ecdf909f729b72dae8a6f25be949d9d017d";
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
