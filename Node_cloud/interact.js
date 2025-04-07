const { Web3 } = require("web3"); 
const contractJson = require("/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"); // Load ABI
const fs = require('fs');
const path = require('path');
const { get } = require("http");
const web3 = new Web3("http://127.0.0.1:8545"); // Besu JSON-RPC

const contractAddress = "0x948B3c478CBc58b592d5bE12eBA3FD31FCfd1e45"; // Replace with your contract address
// const account = "0x71C44C10e3A74133FA4330c3d17aA9DADB9bFE22"; // Replace with your account address
// const privateKey = "def5be7c19dd1d6794b33240d36fa33dea3338d6e473011f47a3282e171326cd"; // Replace with your private key ETH account 

const accountsData = JSON.parse(fs.readFileSync('/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/prefunded_keys.json'));
const account = accountsData.prefunded_accounts[0].address; // Using the first account from the JSON file
const privateKey = accountsData.prefunded_accounts[0].private_key; // Using the first account from the JSON file

const contract = new web3.eth.Contract(contractJson.abi, contractAddress);

///// TRANSACTION FUNCTIONS (Require Signing & Gas) /////

/**
 * Function to Register an IoT Node (Fog, Edge, Sensor, Actuator)
 */
async function registerNode(nodeId, nodeName, senderNodeTypeStr, publicKey, address, receiverNodeTypeStr, nodeSignature) {
    try {
        const txData = contract.methods.registerNode(nodeId, nodeName, senderNodeTypeStr, publicKey, address, receiverNodeTypeStr, nodeSignature).encodeABI();
        let latestNonce = await web3.eth.getTransactionCount(account, 'pending'); // Get latest nonce
        let nonce = Number(latestNonce) + 1; // Convert BigInt to Number and increment
        console.log("Nonce:", nonce);

        const tx = {
            from: account,
            to: contractAddress,
            gas: 3000000,
            gasPrice: '0',
            nonce: latestNonce,
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
            console.log(` Sender Capability Token: ${decodedEvent.senderCapabilityToken}`);
            console.log(` Receiver Capability Token: ${decodedEvent.receiverCapabilityToken}`);
        }

    } catch (error) {
        console.error("Error Registering Node:", error);
    }
}

// registerNode(
//     "FN-009",
//     "Fog Nde 1",
//     "Fog",
//     "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
//     "e39035c0c9ae46f48fdd6325f12787c862a78daf",
//     "Cloud",
//     "0x1234567890abcdef1234567890abcde71234567890abcdef1434567890abcdef"
// );

async function isNodeRegistered(nodeSignature) {
    try {
        const result = await contract.methods.isNodeRegistered(nodeSignature).call();
        console.log(result);
        return result;
    } catch (error) {
        console.error("Error Checking Node Registration (nodeSignature):", error);
        return false;
    }
}

// isNodeRegistered("0x1234567890abcdef1234567890abcee71234567890abcdef1434567890abcdef");
/**
 * Get Details of a Node
 */

async function getNodeDetails(nodeSignature) {
    try {
        const result = await contract.methods.getNodeDetailsBySignature(nodeSignature).call();
        // console.log("Node Details:", result);

        const details = {
            nodeId: result[0],
            nodeName: result[1],
            nodeType: result[2].toString(),
            publicKey: result[3],
            isRegistered: result[4],
            senderCapabilityToken: result[5],
            receiverCapabilityToken: result[6],
            registeredBy: result[7],
            nodeSignature: result[8], // ✅ Includes signature
            registeredByNodeType: result[9].toString(),
        };
        console.log(JSON.stringify(details));
    } catch (error) {
        console.log(JSON.stringify({
            error: "Error fetching node details",
            message: error.message
        }));
    }
}

// getNodeDetails("0x1234567890abcdef1234567890abcde71234567890abcdef1434567890abcdef");

async function getAllTransactions() {
    let latestBlock = await web3.eth.getBlockNumber(); // Get latest block number
    console.log("🔹 Latest Block:", latestBlock);

    for (let i = 0; i <= latestBlock; i++) {
        let block = await web3.eth.getBlock(i, true); // Fetch block details
        if (block.transactions.length > 0) {
            console.log(`\n🟢 Block ${i} contains ${block.transactions.length} transactions:`);

            block.transactions.forEach(tx => {
                console.log(`📌 Tx Hash: ${tx.hash}`);
                console.log(`   🔹 From: ${tx.from}`);
                console.log(`   🔹 To: ${tx.to}`);
                console.log(`   🔹 Value: ${web3.utils.fromWei(tx.value, 'ether')} ETH`);
                console.log(`   🔹 Gas Used: ${tx.gas}`);
                console.log(`   🔹 Nonce: ${tx.nonce}`);
            });
        }
    }
}

// getAllTransactions();


module.exports = {
    registerNode,
    isNodeRegistered,
    getNodeDetails
};

if (require.main === module) {
    const args = process.argv.slice(2);
    const command = args[0];

    (async () => {
        if (command === "isNodeRegistered") {
            const nodeSignature = args[1]; // Now expects address, not nodeId
            const result = await isNodeRegistered(nodeSignature);
        }

        if (command === "getNodeDetails") {
            const nodeSignature = args[1];
            await getNodeDetails(nodeSignature);
        }

        if (command === "registerNode") {
            const [
                nodeId,
                nodeName,
                senderNodeTypeStr,
                publicKey,
                address,
                receiverNodeTypeStr,
                nodeSignature
            ] = args.slice(1);

            await registerNode(
                nodeId,
                nodeName,
                senderNodeTypeStr,
                publicKey,
                address,
                receiverNodeTypeStr,
                nodeSignature
            );
        }
    })();
}