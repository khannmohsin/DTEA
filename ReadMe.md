https://besu.hyperledger.org/private-networks/tutorials/qbft


BESU CONFIG:

1. Create a configuration file (qbftConfigFile.json)
There is a python file to do that (create_QBFTConfig.py)

2. Generate node keys and a genesis file

besu operator generate-blockchain-config --config-file=qbftConfigFile.json --to=networkFiles --private-key-file-name=key

Besu creates the following in the networkFiles directory:

genesis.json - The genesis file including the extraData property specifying the four nodes are validators.
A directory for each node named using the node address and containing the public and private key for each node.
networkFiles/
├── genesis.json
└── keys
    ├── 0x438821c42b812fecdcea7fe8235806a412712fc0
    │   ├── key
    │   └── key.pub
    ├── 0xca9c2dfa62f4589827c0dd7dcf48259aa29f22f5
    │   ├── key
    │   └── key.pub
    ├── 0xcd5629bd37155608a0c9b28c4fd19310d53b3184
    │   ├── key
    │   └── key.pub
    └── 0xe96825c5ab8d145b9eeca1aba7ea3695e034911a
        ├── key
        └── key.pub

3. Copy the genesis file to the main directory and copy the key pair to their associated node directories. 

4. Start the first node (Cloud Node) as the bootnode

besu --data-path=data --genesis-file=../genesis.json --rpc-http-enabled --rpc-http-api=ETH,NET,QBFT --host-allowlist="*" --rpc-http-cors-origins="all" --profile=ENTERPRISE

5. For starting other nodes following are the commands:

besu --data-path=data --genesis-file=../genesis.json --bootnodes=<Node-1 Enode URL> --p2p-port=30304 --rpc-http-enabled --rpc-http-api=ETH,NET,QBFT --host-allowlist="*" --rpc-http-cors-origins="all" --rpc-http-port=8546 --profile=ENTERPRISE


DEPLOYING CONTRACTS USING TRUFFLE:

1. npm install -g truffle

2. npx truffle init

MyBesuProject/
│── contracts/      # Smart contracts (Solidity files)
│── migrations/     # Deployment scripts
│── test/           # Test scripts
│── truffle-config.js  # Truffle configuration file

3. Configure Truffle for Besu

module.exports = {
  networks: {
    besu: {
      host: "127.0.0.1",  // Besu RPC URL
      port: 8545,         // Besu RPC Port
      network_id: 1337,   // Besu Network ID
      gas: 4700000,       // Gas limit
      gasPrice: 0         // No gas fees in private networks
    }
  },
  compilers: {
    solc: {
      version: "0.7.0",  // Use Solidity 0.7.0
    }
  }
};

Note:  Ensure Besu is running before deployment! (besu --data-path=data ...) 

4. Paste your smart contract code (FogNodeRegistry.sol)

MyBesuProject/
│── contracts/      # Smart contracts (Solidity files)

5. npx truffle compile

Compiling your contracts...
> Compiling ./contracts/FogNodeRegistry.sol
> Artifacts written to ./build/contracts
> Compiled successfully!

6. Navigate to migrations/ and create a migration script:

const FogNodeRegistry = artifacts.require("FogNodeRegistry");

module.exports = function (deployer) {
  deployer.deploy(FogNodeRegistry);
};


7. Deploy the Contract on Besu

npx truffle migrate --network besu

Starting migrations...
======================
> Network name:    'besu'
> Network id:      1337
> Block gas limit: 4700000

2_deploy_contracts.js
=====================

   Deploying 'FogNodeRegistry'
   ---------------------------
   > transaction hash:    0xd223d87b0e1f12ea6e...
   > contract address:    0x01EE22E9b18ca331B9e...
   > block number:        205
   > account:             0xD8f6464D101A820D9E8...
   > gas used:            1091075 (0x10a603)

Summary
=======
> Total deployments:   1
> Final cost:          0 ETH






curl -X POST --data '{"jsonrpc":"2.0","method":"qbft_getPendingVotes","params":[],"id":1}' http://127.0.0.1:8545


curl -X POST --data '{
  "jsonrpc":"2.0",
  "method":"qbft_proposeValidatorVote",
  "params":["0xdf641b34498b81a41f3b3d5691e348efd6ea4972", true],
  "id":1
}' http://localhost:8545


curl -X POST --data '{
  "jsonrpc":"2.0",
  "method":"qbft_getValidatorsByBlockNumber",
  "params":["latest"],
  "id":1
}' http://localhost:8545