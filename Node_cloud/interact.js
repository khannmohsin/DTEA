const { Web3 } = require("web3"); 
const path = require('path');
const rootPath = path.resolve(__dirname, '');
const contractJson = require(path.join(rootPath, "data/NodeRegistry.json")); // Load ABI
const fs = require('fs');
const { get } = require("http");
const { send, emit } = require("process");
const rpcURL = "http://127.0.0.1:8545";
const web3 = new Web3(rpcURL)
const fetch = require("node-fetch");
const networkId = Object.keys(contractJson.networks)[0];
const contractAddress = contractJson.networks[networkId].address;
const accountsData = JSON.parse(fs.readFileSync(path.join(rootPath, 'prefunded_keys.json')));
const account = accountsData.prefunded_accounts[0].address; 
const privateKey = accountsData.prefunded_accounts[0].private_key; 
const contract = new web3.eth.Contract(contractJson.abi, contractAddress);

// ----------------------------------TRANSACTIONS----------------------------------------------------
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

        console.log("Tx Sent. Hash:", receipt.transactionHash);

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
            console.log(decodedEvent.validator);
        } else {
            console.log("No ValidatorProposed event found in logs.");
        }

    } catch (error) {
        console.error("Error emitting validator proposal:", error);
    }
}

async function registerNode(nodeId, nodeName, senderNodeTypeStr, publicKey, address, rpcURL, receiverNodeTypeStr, nodeSignature) {
    try {
        const txData = contract.methods.registerNode(
            nodeId, nodeName, senderNodeTypeStr, publicKey, address, rpcURL, receiverNodeTypeStr, nodeSignature
        ).encodeABI();

        let latestNonce = await web3.eth.getTransactionCount(account, 'pending');
        let nonce = Number(latestNonce) + 1;
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

        console.log("Transaction Hash:", receipt.transactionHash);

        for (const log of receipt.logs) {
            if (log.address.toLowerCase() === contractAddress.toLowerCase()) {
                const eventAbi = contractJson.abi.find(e =>
                    e.type === 'event' && web3.eth.abi.encodeEventSignature(e) === log.topics[0]
                );

                if (eventAbi) {
                    const decoded = web3.eth.abi.decodeLog(eventAbi.inputs, log.data, log.topics.slice(1));
                    // console.log(` Event: ${eventAbi.name}`);
                    // console.log(decoded);

                    if (eventAbi.name === "NodeRegistered") {
                        console.log(`Node Signature: ${decoded.nodeSignature}`);
                    }
                }
            }
        }

    } catch (error) {
        console.error("Error Registering Node:", error);
    }
}

async function issueCapabilityToken(fromNodeSignature, toNodeSignature) {
    try {
        const txData = contract.methods.issueToken(fromNodeSignature, toNodeSignature).encodeABI();
        const nonce = await web3.eth.getTransactionCount(account, 'pending');
        const tx = {
            from: account,
            to: contractAddress,
            gas: 300000,
            gasPrice: '0',
            nonce,
            data: txData
        };

        const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
        const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);

        console.log("Token issued. Tx Hash:", receipt.transactionHash);

        const event = receipt.logs.find(log => log.address.toLowerCase() === contractAddress.toLowerCase());
        if (event) {
            const decoded = web3.eth.abi.decodeLog(
                contractJson.abi.find(e => e.name === "TokenIssued").inputs,
                event.data,
                event.topics.slice(1)
            );
            console.log("TokenIssued Event:");
            console.log("-> From Node Signature:", decoded.fromNodeSignature);
            console.log("-> fromType:", decoded.fromType);
            console.log("-> From Signature:", decoded.fromNodeSignature);
            console.log("-> To Signature:", decoded.toNodeSignature);
            console.log("-> Policy:", decoded.policy);
            console.log("-> Issued At:", new Date(Number(decoded.issuedAt) * 1000).toISOString());
        }

    } catch (error) {
        console.error("Error issuing token:", error.message);
    }
}

async function revokeCapabilityToken(fromNodeSignature, toNodeSignature) {
    try {
        const txData = contract.methods.revokeToken(fromNodeSignature, toNodeSignature).encodeABI();
        const latestNonce = await web3.eth.getTransactionCount(account, 'pending');

        const tx = {
            from: account,
            to: contractAddress,
            gas: 200000,
            gasPrice: '0',
            nonce: latestNonce,
            data: txData
        };

        const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
        const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);

        console.log("Token revoked. Tx Hash:", receipt.transactionHash);

        const log = receipt.logs.find(log => log.address.toLowerCase() === contractAddress.toLowerCase());
        if (log) {
            const decoded = web3.eth.abi.decodeLog(
                contractJson.abi.find(e => e.name === "TokenRevoked").inputs,
                log.data,
                log.topics.slice(1)
            );
            console.log("TokenRevoked Event:");
            console.log("-> From Signature:", decoded.fromNodeSignature);
            console.log("-> To Signature:", decoded.toNodeSignature);
        }
    } catch (error) {
        console.error("Error revoking token:", error.message);
    }
}

// ----------------------------------NODE RELATED FUNCTIONS----------------------------------------------------------------


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

async function getNodeDetails(nodeSignature) {
    try {
        const result = await contract.methods.getNodeDetailsBySignature(nodeSignature).call();
        const details = {
            nodeId: result[0],
            nodeName: result[1],
            nodeType: result[2].toString(),
            publicKey: result[3],
            isRegistered: result[4],
            registeredBy: result[5],
            nodeSignature: result[6],
            registeredByNodeType: result[7].toString(),
        };
        console.log(JSON.stringify(details));
    } catch (error) {
        console.log(JSON.stringify({
            error: "Error fetching node details",
            message: error.message
        }));
    }
}

async function getNodeDetailsByAddress(nodeAddress) {
    try {
        const details = await contract.methods.getNodeDetailsByAddress(nodeAddress).call();

        console.log("Node Details:");
        console.log("-> Node ID:", details[0]);
        console.log("-> Node Name:", details[1]);
        console.log("-> Node Type (Enum Index):", details[2]); 
        console.log("-> Public Key:", details[3]);
        console.log("-> Is Registered:", details[4]);
        console.log("-> Registered By (address):", details[5]);
        console.log("-> Node Signature:", details[6]);
        console.log("-> Registered By Node Type (Enum Index):", details[7]);
    } catch (error) {
        console.error("Error fetching node details by address:", error.message);
    }
}

// ----------------------------------TOKEN RELATED FUNCTIONS----------------------------------------------------------------

// async function callTokenCheck(fromNodeSignature) {
//     try {
//         const txData = contract.methods.issueToken(fromNodeSignature).encodeABI();
//         const latestNonce = await web3.eth.getTransactionCount(account, "pending");
//         let nonce = Number(latestNonce) + 1; // Convert BigInt to Number and increment
//         console.log("Nonce:", nonce);
//         const tx = {
//             from: account,
//             to: contractAddress,
//             data: txData,
//             gas: 200000,
//             gasPrice: "0",
//             nonce
//         };

//         const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
//         const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);

//         console.log("Tx Sent. Hash:", receipt.transactionHash);

//         const eventAbi = contractJson.abi.find(e => e.name === "TokenChecked");
//         const eventLog = receipt.logs.find(log => log.topics[0] === web3.eth.abi.encodeEventSignature(eventAbi));

//         if (eventLog) {
//             const decoded = web3.eth.abi.decodeLog(
//                 eventAbi.inputs,
//                 eventLog.data,
//                 eventLog.topics.slice(1)
//             );

//             console.log("TokenChecked Event:");
//             console.log("-> From Node Signature:", decoded.fromNodeSignature);
//             console.log("-> To Node ID:", decoded.toNodeSignature); // `toNodeId` passed as second arg in event
//         } else {
//             console.log("TokenChecked event not found in logs.");
//         }

//     } catch (err) {
//         console.error("Error calling issueToken:", err.message);
//     }
// }


async function getCapabilityToken(fromNodeSignature, toNodeSignature) {
    try {
        const token = await contract.methods.getToken(fromNodeSignature, toNodeSignature).call();

        console.log("Capability Token Info:");
        console.log("Policy:", token.policy);
        console.log("-> Issued At (Unix):", token.issuedAt.toString());
        console.log("-> Issued At (UTC):", new Date(Number(token.issuedAt) * 1000).toISOString());
        console.log("-> Is Issued:", token.isIssued);
        console.log("-> Is Revoked:", token.isRevoked);
    } catch (error) {
        console.error("Error fetching token:", error.message);
    }
}

async function checkCapabilityToken(fromNodeSignature, toNodeSignature) {
    try {
        const isValid = await contract.methods.checkToken(fromNodeSignature, toNodeSignature).call();
        console.log(isValid); 
    } catch (error) {
        console.error("Error checking token:", error.message);
    }
}

    
async function checkTokenExpiry(fromNodeSignature, toNodeSignature, validityPeriodInSeconds) {
    try {
        const isExpired = await contract.methods
            .isTokenExpired(fromNodeSignature, toNodeSignature, validityPeriodInSeconds)
            .call();

        // console.log(`Token Expiry Check`);
        // console.log(`→ From: ${fromNodeSignature}`);
        // console.log(`→ To: ${toNodeSignature}`);
        // console.log(`→ Validity Period: ${validityPeriodInSeconds} seconds`);
        console.log(`${isExpired}`);
    } catch (error) {
        console.error("Error checking token expiry:", error.message);
    }
}

// ----------------------------------VALIDATOR RELATED FUNCTIONS----------------------------------------------------------------

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

async function watchValidatorProposals() {

    try {
        const pastEvents = await contract.getPastEvents('ValidatorProposed', {
            fromBlock: 0,
            toBlock: 'latest'
        });

        for (const event of pastEvents) {
            console.log(event.returnValues.validator);
        }

    } catch (err) {
        console.error("Error during event handling:", err);
    }
}

async function proposeValidatorVote(validatorAddress, add) {
    try {
        const payload = {
            jsonrpc: "2.0",
            method: "qbft_proposeValidatorVote",
            params: [validatorAddress, add],
            id: 1
        };

        const response = await fetch(rpcURL, {
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

async function getValidatorsByBlockNumber(rpcUrl) {
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


// ----------------------------------GENERAL FUNCTIONS----------------------------------------------------------------

async function getAllTransactions() {
    let latestBlock = await web3.eth.getBlockNumber(); // Get latest block number
    console.log("🔹 Latest Block:", latestBlock);

    for (let i = 0; i <= latestBlock; i++) {
        let block = await web3.eth.getBlock(i, true);
        if (block.transactions.length > 0) {
            console.log(`Block ${i} contains ${block.transactions.length} transactions:`);

            block.transactions.forEach(tx => {
                console.log(`-> Tx Hash: ${tx.hash}`);
                console.log(`-> From: ${tx.from}`);
                console.log(`-> To: ${tx.to}`);
                console.log(`-> Value: ${web3.utils.fromWei(tx.value, 'ether')} ETH`);
                console.log(`-> Gas Used: ${tx.gas}`);
                console.log(`-> Nonce: ${tx.nonce}`);
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

async function getPeerCount(rpcURL) {
    const payload = {
        jsonrpc: "2.0",
        method: "net_peerCount",
        params: [],
        id: 1
    };

    try {
        const response = await fetch(rpcURL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.result) {
            const peerCount = parseInt(data.result, 16); 
            console.log(`${peerCount}`);
            return peerCount;
        } else {
            console.error("Error in response:", data);
            return 0;
        }
    } catch (err) {
        console.error("Failed to fetch peer count:", err.message);
        return 0;
    }
}

module.exports = {
    registerNode,
    isNodeRegistered,
    getNodeDetails,
    checkIfDeployed,
    proposeValidatorVote,
    isValidator,
    getAllTransactions,
    issueCapabilityToken,
    revokeCapabilityToken,
    getCapabilityToken,
    checkTokenExpiry,
    watchValidatorProposals,
    emitValidatorProposalToChain,
    getPeerCount,
    checkCapabilityToken

};

if (require.main === module) {
    
    const args = process.argv.slice(2);
    const command = args[0];

    (async () => {

        if (command === "listenForValidatorProposals") {
            watchValidatorProposals();
        }

        if (command === "issueCapabilityToken") {
            const fromNodeSignature = args[1];
            const toNodeSignature = args[2];
            await issueCapabilityToken(fromNodeSignature, toNodeSignature);
        }

        if (command === "checkCapabilityToken") {
            const fromNodeSignature = args[1];
            const toNodeSignature = args[2];
            await checkCapabilityToken(fromNodeSignature, toNodeSignature);
        }
        if (command === "revokeCapabilityToken") {
            const fromNodeSignature = args[1];
            const toNodeSignature = args[2];
            await revokeCapabilityToken(fromNodeSignature, toNodeSignature);
        }
        if (command === "getCapabilityToken") {
            const fromNodeSignature = args[1];
            const toNodeSignature = args[2];
            await getCapabilityToken(fromNodeSignature, toNodeSignature);
        }
        if (command === "checkTokenExpiry") {
            const fromNodeSignature = args[1];
            const toNodeSignature = args[2];
            const validityPeriodInSeconds = args[3];
            await checkTokenExpiry(fromNodeSignature, toNodeSignature, validityPeriodInSeconds);
        }
        if (command === "getAllTransactions") {
            await getAllTransactions();
        }

        if (command === "getPeerCount") {
            const peerCount = await getPeerCount(rpcURL);
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
            await getValidatorsByBlockNumber(rpcURL);
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
                rpcURL,
                receiverNodeTypeStr,
                nodeSignature
            ] = args.slice(1);

            await registerNode(
                nodeId,
                nodeName,
                senderNodeTypeStr,
                publicKey,
                address,
                rpcURL,
                receiverNodeTypeStr,
                nodeSignature
            );
        }
    })();
}

// registerNode(
//     "CL-001",
//     "Cloud_Node",
//     "Cloud",
//     "0xf5eb61ca22b851ac62a70f60104f829100c5ef9e25f2cdc1c2a142c76e7200199b7f55fc98187841b2c14492e58b74a591f15f038eecc86a866cc8fb67bd2bd3",
//     "0x7ddba9f4032c56847d4858de57d0635af6c8c813",
//     "http://127.0.0.1:8445",
//     "Cloud",
//     "0xc9634487b2cd6d1f938b9a6c26487f7ee66c8e44f04c561c41f36dbb06fa21bc6be3df8323270a2d3c8d45eb589b9199ca7756922c6f7c70bd65f8bc877f3ea700"
// );