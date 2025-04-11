const { Web3 } = require("web3"); 
const contractJson = require("/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_cloud/data/NodeRegistry.json"); // Load ABI
const fs = require('fs');
const path = require('path');
const { get } = require("http");
const { send, emit } = require("process");
const web3 = new Web3("http://127.0.0.1:8545"); // Besu JSON-RPC

const contractAddress = "0x2bD48779860D87F4111A59274F16a142D9519102"; // Replace with your contract address
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
            console.log(` Node Signature: ${decodedEvent.nodeSignature}`);
        }

    } catch (error) {
        console.error("Error Registering Node:", error);
    }
}

// registerNode(
//     "FN-010",
//     "Fog Nde 1",
//     "Fog",
//     "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
//     "e39035c0c9ae46f48fdd6325f12787c862a78daf",
//     "Edge",
//     "0x3434568998afd7ef1234567890abcde71234567890abcdef1434567890abcdef"
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

// isNodeRegistered("0x435c1648ffa09c0927d7cac44ca80180eb1a4f2eaf33c5fae1b00654fd4c7d4c5050a4a2956dc20ff68f3a9fcd18109da00daa2f952fe63fb6628b0efb9d1ac001");



async function sendConsensusTriggerTransaction() {
    try {
        const latestNonce = await web3.eth.getTransactionCount(account, 'pending');
        const tx = {
            from: "0xC0FAee0CBf5ff0139B0DBE121626f837EE86725c",
            to: "0xC0FAee0CBf5ff0139B0DBE121626f837EE86725c", // send to self
            value: '0x0',
            gas: 21000,
            gasPrice: '3000',
            nonce: latestNonce
        };

        const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
        const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);
        console.log("Dummy Tx Sent. Hash:", receipt.transactionHash);
    } catch (error) {
        console.error("Error sending dummy transaction:", error);
    }
}

// sendConsensusTriggerTransaction();

// sendDummyTransaction();


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


// getNodeDetails("0x1234569870abddef1234567890abcde71234567890abcdef1434567890abcdef");


async function isValidator(nodeSignature) {
    try {
        const result = await contract.methods.isValidator(nodeSignature).call();
        console.log(result);
        return result;
    } catch (error) {
        console.log("false");
        return false;
    }
}

// isValidator("0x9d4222e21bd4e24136f97d270efc1c4fe0bc8b7acbde1e063ad7fe7eaa77650f0c9c20b45210bd59da088c43b26b7a1181e86d0bf7ab0d62aefc584b2628b49c01");

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

async function checkIfDeployed(contractAddress) {
    try {
        const code = await web3.eth.getCode(contractAddress);
        const isDeployed = code !== '0x' && code !== '0x0';
        console.log(isDeployed);
        return isDeployed;
    } catch (error) {
        console.log("false");
        return false;
    }
}


async function watchValidatorProposals() {
    console.log("Fetching past ValidatorProposed events...");

    try {
        // Step 1: Fetch past events
        const pastEvents = await contract.getPastEvents('ValidatorProposed', {
            fromBlock: 0,
            toBlock: 'latest'
        });

        for (const event of pastEvents) {
            console.log("Past Event:");
            console.log("   Proposed By:", event.returnValues.proposedBy);
            console.log("   Validator:", event.returnValues.validator);
            console.log("   Tx Hash:", event.transactionHash);
        }

        // Step 2: Subscribe to future events
        // console.log("Listening for new ValidatorProposed events...");
        // contract.events.ValidatorProposed({ fromBlock: 'latest' })
        //     .on('data', (event) => {
        //         console.log("   New Event Detected:");
        //         console.log("   Proposed By:", event.returnValues.proposedBy);
        //         console.log("   Validator:", event.returnValues.validator);
        //         console.log("   Tx Hash:", event.transactionHash);
        //     })
        //     .on('error', (error) => {
        //         console.error("Error listening to ValidatorProposed:", error);
        //     });

    } catch (err) {
        console.error("Error during event handling:", err);
    }
}

// watchValidatorProposals();

async function emitValidatorProposalToChain(validatorAddress) {
    try {
        const txData = contract.methods.proposeValidator(validatorAddress).encodeABI();
        const nonce = await web3.eth.getTransactionCount(account, "pending");

        const tx = {
            from: account,
            to: contractAddress,
            gas: 100000,
            gasPrice: "0",
            nonce: nonce,
            data: txData
        };

        const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
        const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);

        console.log("✅ Tx Sent. Hash:", receipt.transactionHash);

        // 👇 Extract the emitted event from receipt logs
        const decodedEvent = receipt.logs
            .map(log => {
                try {
                    return web3.eth.abi.decodeLog(
                        contractJson.abi.find(e => e.name === "ValidatorProposed").inputs,
                        log.data,
                        log.topics.slice(1)
                    );
                } catch (e) {
                    return null;
                }
            })
            .find(e => e !== null);

        if (decodedEvent) {
            console.log("🎯 ValidatorProposed Event:");
            console.log("Proposed By:", decodedEvent.proposedBy);
            console.log("Validator:", decodedEvent.validator);
        } else {
            console.log("No ValidatorProposed event found in logs.");
        }

    } catch (error) {
        console.error("❌ Error emitting validator proposal:", error);
    }
}

// emitValidatorProposalToChain("0x9ec1a623566117361454b0ef2b676115ef12991b");

async function proposeValidatorVote(validatorAddress, add) {
    try {
        const payload = {
            jsonrpc: "2.0",
            method: "qbft_proposeValidatorVote",
            params: [validatorAddress, add],
            id: 1
        };

        const response = await fetch("http://127.0.0.1:8545", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        console.log("Vote submitted:", data);
    } catch (error) {
        console.error("Error proposing validator vote:", error);
    }
}


// proposeValidatorVote("0x8ec1a623566117361454b0ef2b676115ef12991b", true);

async function getValidatorsByBlockNumber(rpcUrl = "http://127.0.0.1:8545") {
    try {
        const payload = {
            jsonrpc: "2.0",
            method: "qbft_getValidatorsByBlockNumber",
            params: ["latest"],
            id: 1
        };

        const response = await fetch(rpcUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (data.result) {
            console.log(data.result);
            return data.result;
        } else {
            console.error("Error retrieving validators:", data);
            return [];
        }
    } catch (error) {
        console.error("Failed to get validators:", error);
        return [];
    }
}

// getValidatorsByBlockNumber();


// checkIfDeployed(contractAddress)

module.exports = {
    registerNode,
    isNodeRegistered,
    getNodeDetails,
    checkIfDeployed,
    proposeValidatorVote
    // listenForValidatorProposals

};

if (require.main === module) {
    
    const args = process.argv.slice(2);
    const command = args[0];

    (async () => {

        if (command === "listenForValidatorProposals") {
            watchValidatorProposals();
        }

        if (command === "isNodeRegistered") {
            const nodeSignature = args[1]; // Now expects address, not nodeId
            const result = await isNodeRegistered(nodeSignature);
        }

        if (command === "getNodeDetails") {
            const nodeSignature = args[1];
            await getNodeDetails(nodeSignature);
        }

        if (command === "checkIfDeployed") {
            await checkIfDeployed(contractAddress);
        }

        if (command === "isValidator") {
            const nodeSignature = args[1];
            await isValidator(nodeSignature);
        }
        
        if (command === "getValidatorsByBlockNumber") {
            await getValidatorsByBlockNumber();
        }
        if (command === "emitValidatorProposalToChain") {
            const validatorAddress = args[1];
            if (!validatorAddress.startsWith("0x")) {
                console.error("Validator address must start with '0x'!");
                process.exit(1);
            }
            await emitValidatorProposalToChain(validatorAddress);
        }

        if (command === "proposeValidatorVote") { 
            const validatorAddress = args[1];
            if (!validatorAddress.startsWith("0x")) {
                console.error("Validator address must start with '0x'!");
                process.exit(1);
            }
            const voteArg = args[2];
            if (voteArg !== "true" && voteArg !== "false") {
                console.error("Invalid vote argument! Use 'true' to add or 'false' to remove a validator.");
                process.exit(1);
            }
            await proposeValidatorVote(validatorAddress, voteArg);
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