const { Web3 } = require("web3"); 
const contractJson = require("/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"); // Load ABI
const fs = require('fs');
const path = require('path');
const { get } = require("http");
const web3 = new Web3("http://127.0.0.1:8545"); // Besu JSON-RPC

const contractAddress = "0xfc19082A440e416D2744a22cf6965d90c8C2b0f9"; // Replace with your contract address
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
async function registerNode(nodeId, nodeName, nodeTypeStr, publicKey, address) {
    try {
        const txData = contract.methods.registerNode(nodeId, nodeName, nodeTypeStr, publicKey, address).encodeABI();
        let latestNonce = await web3.eth.getTransactionCount(account, 'pending'); // Get latest nonce
        let nonce = Number(latestNonce) + 1; // Convert BigInt to Number and increment
        console.log("🔹 Nonce:", nonce);

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
            console.log(`🔑 Capability Token: ${decodedEvent.capabilityToken}`);
        }

    } catch (error) {
        console.error("❌ Error Registering Node:", error);
    }
}

async function isNodeRegistered(nodeId) {
    try {
        const result = await contract.methods.isNodeRegistered(nodeId).call();
        return result;
    } catch (error) {
        console.error("Error Checking Node Registration:", error);
        console.log("ERROR");
    }
}

/**
 * Get Details of a Node
 */
async function getNodeDetails(nodeId) {
    try {
        const result = await contract.methods.getNodeDetails(nodeId).call();
        console.log(`🔍 Node Details:`, {
            nodeName: result[0],
            nodeType: result[1],
            publicKey: result[2],
            isRegistered: result[3],
            capabilityToken: result[4]
        });
    } catch (error) {
        console.error("Error Fetching Node Details:", error);
    }
}

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
                console.log("------------------------------------------------");
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