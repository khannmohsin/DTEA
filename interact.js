const { Web3 } = require("web3");
const contractJson = require("./build/contracts/NodeRegistry.json"); // Load ABI
const web3 = new Web3("http://127.0.0.1:8545"); // Besu JSON-RPC

const contractAddress = "0xcA7334528Ca388e9b9a5F0064aFCd699edFd6eB7"; // Replace with your contract address
const account = "0x23833a9574E173BF2D9254F03a2ecBA2ECC20950"; // Replace with your account address
const privateKey = "a803de60f3b8de4acdc6abb5bc080f2cd4168cb633e4a38019b27c64c3d3439d"; // ⚠️ Do not expose private key

const contract = new web3.eth.Contract(contractJson.abi, contractAddress);


async function getAllTransactions() {
    let latestBlock = await web3.eth.getBlockNumber();
    console.log("Latest Block:", latestBlock);

    for (let i = 0; i <= latestBlock; i++) {
        let block = await web3.eth.getBlock(i, true);
        if (block.transactions.length > 0) {
            console.log(`Block ${i} has ${block.transactions.length} transactions:`);
            block.transactions.forEach(tx => {
                console.log(`Tx Hash: ${tx.hash}, From: ${tx.from}, To: ${tx.to}, Value: ${web3.utils.fromWei(tx.value, 'ether')} ETH`);
            });
        }
    }
}

// getAllTransactions();


async function registerNode(nodeId, nodeName, nodeTypeStr, publicKey) {
    try {
        const txData = contract.methods.registerNode(nodeId, nodeName, nodeTypeStr, publicKey).encodeABI();
        const nonce = await web3.eth.getTransactionCount(account, 'pending');

        const tx = {
            from: account,
            to: contractAddress,
            gas: 3000000,
            gasPrice: '0',
            nonce: nonce,
            data: txData
        };

        const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
        const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);

        console.log("Node Registered! Transaction Hash:", receipt.transactionHash);

        // Extract event logs to get the token
        const event = receipt.logs.find(log => log.address.toLowerCase() === contractAddress.toLowerCase());
        if (event) {
            const decodedEvent = web3.eth.abi.decodeLog(
                contractJson.abi.find(e => e.name === "NodeRegistered").inputs,
                event.data,
                event.topics.slice(1)
            );
            console.log(`Capability Token: ${decodedEvent.capabilityToken}`);
        }

    } catch (error) {
        console.error("Error Registering Node:", error);
    }
}

// // ✅ Call the function to register a new Fog Node
// registerNode(
//     "fog-002",
//     "Fog Node 2",
//     "Fog", // Node type as a string (Fog, Edge, Sensor, Actuator)
//     "0x123abasdasc456def789ghi"
// );


/**
 * ✅ Function to Get Node Details
 */
async function getNodeDetails(nodeId) {
    try {
        const result = await contract.methods.getNodeDetails(nodeId).call();
        
        // Convert the enum NodeType from number to a readable format
        const nodeTypes = ["Unknown", "Fog", "Edge", "Sensor", "Actuator"];
        const nodeTypeString = nodeTypes[result[1]] || "Invalid";

        console.log(`🔍 Node Details for ${nodeId}:`);
        console.log({
            nodeName: result[0],
            nodeType: nodeTypeString,
            publicKey: result[2],
            isRegistered: result[3],
            capabilityToken: result[4]  // ✅ Token stored here
        });

    } catch (error) {
        console.error("Error Fetching Node Details:", error);
    }
}

/**
 * Call the function to get details of a registered node
 */
getNodeDetails("fog-001");