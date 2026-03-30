const HDWalletProvider = require("@truffle/hdwallet-provider");

// Load private key and RPC URL
const privateKey = "8d37794733333afe68600a12b1e8295b4d2d0874f4dc42bbb59256b08bcd4bdb";
const besuRpcUrl = "http://127.0.0.1:8745";

module.exports = {
  networks: {
    besuWallet: {
      provider: () => new HDWalletProvider(privateKey, besuRpcUrl),
      network_id: "*",  // Accept any network ID
      gas:  29000000,  // Increase gas limit
      gasPrice: 0,  // Set Besu to allow 0 gas for private networks
      confirmations: 0,  // Number of confirmations to wait between deployments
      timeoutBlocks: 200,  // Number of blocks before a deployment times out
      skipDryRun: true,  // Skip dry run before migrations
    },
  },
  compilers: {
    solc: {
      version: "0.8.4", 
      settings: {
        optimizer: {
          enabled: true,  // Enable the optimizer
          runs: 20,  // Number of runs for the optimizer 
        },
      },
    },
  },
};
