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

// console.log("Contract Address:", contractAddress);
// const contractAddress = "0xEfe311B353970D74F503047dF4F15e93f2388717"; // Replace with your contract address
// const account = "0x71C44C10e3A74133FA4330c3d17aA9DADB9bFE22"; // Replace with your account address
// const privateKey = "def5be7c19dd1d6794b33240d36fa33dea3338d6e473011f47a3282e171326cd"; // Replace with your private key ETH account 

const accountsData = JSON.parse(fs.readFileSync(path.join(rootPath, 'prefunded_keys.json')));
const account = accountsData.prefunded_accounts[0].address; // Using the first account from the JSON file
// console.log("Account Address:", account);
const privateKey = accountsData.prefunded_accounts[0].private_key; // Using the first account from the JSON file

const contract = new web3.eth.Contract(contractJson.abi, contractAddress);

///// TRANSACTION FUNCTIONS (Require Signing & Gas) /////

/**
 * Function to Register an IoT Node (Fog, Edge, Sensor, Actuator)
 */
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

        console.log("Node Registered! Transaction Hash:", receipt.transactionHash);

        // 🔧 MODIFIED: Decode all logs properly by matching the correct event ABI
        for (const log of receipt.logs) {
            if (log.address.toLowerCase() === contractAddress.toLowerCase()) {
                const eventAbi = contractJson.abi.find(e =>
                    e.type === 'event' && web3.eth.abi.encodeEventSignature(e) === log.topics[0]
                );

                if (eventAbi) {
                    const decoded = web3.eth.abi.decodeLog(eventAbi.inputs, log.data, log.topics.slice(1));
                    console.log(` Event: ${eventAbi.name}`);
                    console.log(decoded);

                    // 🔧 OPTIONAL: Custom log if it's NodeRegistered
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
// issueCapabilityToken("0xa00af5bc1a3482dd5c6a75ca3d58eddcb8b308fc7e4d54f646b2fb5b5e35eb796078266503d24abe91faea781e894ce8135fcd5ccb0331cfa4e47a18d99c692900", 
//     "0xabedde3df33d338a55d474a3966807fac7ff9496e1474743446538864e06d5036d0779a59923323f59ab222414139cfd89cc443aa83e6bf1e7ccef4254e178e401");

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

// Fog: Sig: 0x6634598998aed7ef1734567890abcde71234567891abcdef1434567890abcdef Add: e59035c0c9ae46f49fdd6325f12787c862a78eaf
// Cloud : Sig: 0x8834598998aed7ef1734567890abcde71234567891abcdef1434567890abcdef Add: e98035c0c9ae46f49fdd6325f12787c862a78eaf

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

// isNodeRegistered("0x8cabefbdd658dae3247decc1d7c7fc579b755a5fa753cd7d4481c891975b30bb123f8ab9ad8e86ac161893967bc33e3debd1bf20d7e40ff4ef29e62c1b33b9ff01");

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
            // console.log("ValidatorProposed Event:");
            // console.log("Proposed By:", decodedEvent.proposedBy);
            console.log(decodedEvent.validator);
        } else {
            console.log("No ValidatorProposed event found in logs.");
        }

    } catch (error) {
        console.error("Error emitting validator proposal:", error);
    }
}

// emitValidatorProposalToChain("0x9ec1a623566117361454b0ef2b676115ef12991b");
// issueCapabilityToken("0xd4b9253b6f1b77febabb78e956ea0011ce8c8121ae1211929c78114fea0352b0768a5beed214fcaf13f605c164db2371038893ce7c316fd2d66539b7b74b66b501", 
//     "0xf1b07584eb76759b4bb7d9f7e14f3fad5b412e578cb53122b4357e0298b70f9c6969419224e6e8bd43ba4fcc227541bcbcd53b6517c532632cce8b09b5d46fd901"
// );
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
            // senderCapabilityToken: result[5],
            // receiverCapabilityToken: result[6],
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

        console.log("📦 Node Details:");
        console.log("🆔 Node ID:", details[0]);
        console.log("🏷️ Node Name:", details[1]);
        console.log("🔧 Node Type (Enum Index):", details[2]); // You may map this index to the actual string
        console.log("🔑 Public Key:", details[3]);
        console.log("✅ Is Registered:", details[4]);
        console.log("📝 Registered By (address):", details[5]);
        console.log("🧾 Node Signature:", details[6]);
        console.log("📎 Registered By Node Type (Enum Index):", details[7]);
    } catch (error) {
        console.error("❌ Error fetching node details by address:", error.message);
    }
}

// getNodeSignatureBySender();
// Example call
// getNodeDetailsByAddress("0xcd472fdc3ef798c933c2e3d2123a20861bbded27");




async function issueCapabilityToken(fromNodeSignature, toNodeSignature) {
    try {
        const txData = contract.methods.issueToken(fromNodeSignature, toNodeSignature).encodeABI();
        // console.log("Transaction Data:", txData);

        const nonce = await web3.eth.getTransactionCount(account, 'pending');
        // let nonce = Number(latestNonce) + 1; // Convert BigInt to Number and increment
        // console.log("Nonce:", nonce);
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

        // Decode the TokenIssued event
        const event = receipt.logs.find(log => log.address.toLowerCase() === contractAddress.toLowerCase());
        if (event) {
            const decoded = web3.eth.abi.decodeLog(
                contractJson.abi.find(e => e.name === "TokenIssued").inputs,
                event.data,
                event.topics.slice(1)
            );
            console.log("TokenIssued Event:");
            console.log(" From Node Signature:", decoded.fromNodeSignature);
            console.log(" fromType:", decoded.fromType);
            console.log(" From Signature:", decoded.fromNodeSignature);
            console.log(" To Signature:", decoded.toNodeSignature);
            console.log(" Policy:", decoded.policy);
            console.log(" Issued At:", new Date(Number(decoded.issuedAt) * 1000).toISOString());
        }

    } catch (error) {
        console.error("Error issuing token:", error.message);
    }
}

// getNodeDetails("0x379ab0e1e4001800799ac0f5fed5f45e2d7d39dcae7044df7ce4f5aa204bf0af4d3a5a8d02f32361597031921079279365ac13d7295ef5fa48828fb7afcf3bbc01");


// issueCapabilityToken("0xf4a569be482e96cb56fad00e724f338d94c8b4946196795cc83d9cf208d907d9233ecc4c691aea511d8739b5d745c8104003a65be104b4ee87df44ecf70e6f0a00", 
//     "0xf9ddb088bb5ff1b4be543127e1b30b4432852c78ea7ed222018ba8dff76acff1387c26409185a802d1f8b9591e31ff6108ed0e792a055824ec13e9a0ac94f8aa01"
// );

// issueCapabilityToken("0x71b79b3d40ae3274da9bc523542bc1f69ce6b4bc3662a96ba700716a241fc2bb2792123058ee68f90f2845f751254466081c6fdad0ac03d68b6901e721e226b201", 
//     "0xa9aa1acde6b804fa6c2545c698aadf3d9478e8e5c5ed677004f70b243f4eaf767ee7b587871654efa5d7f1f6804022aab5440eba9afb38d1b8c75b0fb6e5bcbf01");

async function callTokenCheck(fromNodeSignature) {
    try {
        const txData = contract.methods.issueToken(fromNodeSignature).encodeABI();
        const latestNonce = await web3.eth.getTransactionCount(account, "pending");
        let nonce = Number(latestNonce) + 1; // Convert BigInt to Number and increment
        console.log("Nonce:", nonce);
        const tx = {
            from: account,
            to: contractAddress,
            data: txData,
            gas: 200000,
            gasPrice: "0",
            nonce
        };

        const signedTx = await web3.eth.accounts.signTransaction(tx, privateKey);
        const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);

        console.log("✅ Tx Sent. Hash:", receipt.transactionHash);

        // Decode the TokenChecked event
        const eventAbi = contractJson.abi.find(e => e.name === "TokenChecked");
        const eventLog = receipt.logs.find(log => log.topics[0] === web3.eth.abi.encodeEventSignature(eventAbi));

        if (eventLog) {
            const decoded = web3.eth.abi.decodeLog(
                eventAbi.inputs,
                eventLog.data,
                eventLog.topics.slice(1)
            );

            console.log("📡 TokenChecked Event:");
            console.log(" From Node Signature:", decoded.fromNodeSignature);
            console.log(" To Node ID:", decoded.toNodeSignature); // `toNodeId` passed as second arg in event
        } else {
            console.log("⚠️ TokenChecked event not found in logs.");
        }

    } catch (err) {
        console.error("❌ Error calling issueToken:", err.message);
    }
}

// Example call
// callTokenCheck("0x0998d949f2147f9cb61fcfb097f830a878de056833b57c66ad7d58810118a6855f8205fad664a2fe671daa83bfc0beaa049a52ff7f9d8467ea40c390592929a001");


// issueCapabilityToken("0x0998d949f2147f9cb61fcfb097f830a878de056833b57c66ad7d58810118a6855f8205fad664a2fe671daa83bfc0beaa049a52ff7f9d8467ea40c390592929a001");


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

        // Decode and log event
        const log = receipt.logs.find(log => log.address.toLowerCase() === contractAddress.toLowerCase());
        if (log) {
            const decoded = web3.eth.abi.decodeLog(
                contractJson.abi.find(e => e.name === "TokenRevoked").inputs,
                log.data,
                log.topics.slice(1)
            );
            console.log("TokenRevoked Event:");
            console.log("From Signature:", decoded.fromNodeSignature);
            console.log("To Signature:", decoded.toNodeSignature);
        }
    } catch (error) {
        console.error("Error revoking token:", error.message);
    }
}

// Example call
// issueCapabilityToken("0x358427677a403f015e9702e9ad700a7994a1ab1062b59f92d180eb9ab75b276e1ce1cc2fe09cf1bfee5e2b710fb9788ff275fbb73f020106a09557572bb9d94000",
//     "0x379ab0e1e4001800799ac0f5fed5f45e2d7d39dcae7044df7ce4f5aa204bf0af4d3a5a8d02f32361597031921079279365ac13d7295ef5fa48828fb7afcf3bbc01");


async function getCapabilityToken(fromNodeSignature, toNodeSignature) {
    try {
        const token = await contract.methods.getToken(fromNodeSignature, toNodeSignature).call();

        console.log("Capability Token Info:");
        console.log("Policy:", token.policy);
        console.log("Issued At (Unix):", token.issuedAt.toString());
        console.log("Issued At (UTC):", new Date(Number(token.issuedAt) * 1000).toISOString());
        console.log("Is Issued:", token.isIssued);
        console.log("Is Revoked:", token.isRevoked);
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

async function watchRpcUrlMappings() {
    try {
        const fromBlock = 0;
        const toBlock = 'latest';

        const pastEvents = await contract.getPastEvents('RpcUrlMapped', {
            fromBlock,
            toBlock
        });

        if (pastEvents.length === 0) {
            console.log("No RpcUrlMapped events found in the specified range.");
        }

        for (const event of pastEvents) {
            const nodeAddress = event.returnValues.nodeAddress;
            const rpcURL = event.returnValues.rpcURL;
            console.log(`${nodeAddress} : ${rpcURL}`);
        }

        // console.log("📡 Listening for new RpcUrlMapped events...");
        // contract.events.RpcUrlMapped({ fromBlock: 'latest' })
        //     .on('data', (event) => {
        //         const nodeAddress = event.returnValues.nodeAddress;
        //         const rpcURL = event.returnValues.rpcURL;
        //         console.log(`🆕 New RPC URL Mapped: ${nodeAddress} => ${rpcURL}`);
        //     })
        //     .on('error', (error) => {
        //         console.error("🚨 Error listening to RpcUrlMapped:", error);
        //     });

    } catch (err) {
        console.error("Error during RpcUrlMapped event handling:", err);
    }
}

// watchRpcUrlMappings();

// issueCapabilityToken("0xac649b22c3e2decd3b40c9a4762347187fbbdc985879de0373d5b2a6e0b4b2ea5c344a7d20b9cff28e425c63f5bb5d8e25d4a47c97e794618c57bb97813efed400", 
//     "0x1eb6c79dc71238e884ab5631c71f762b372259351776dead81094e8ef26a5009385726bd5394b08fccfdd17ebb476035f2d818bc4c721cc237d9574c81c0e35b00"
// );


// revokeCapabilityToken("0x47adfad83202c4e3591964405c8ccdaaf449443a3c02177d53f5b3351014624203156c4cfe0f94880ed0942c9100c754c1b841888ce5d5fc2a623790728072db01", 
//     "0x0998d949f2147f9cb61fcfb097f830a878de056833b57c66ad7d58810118a6855f8205fad664a2fe671daa83bfc0beaa049a52ff7f9d8467ea40c390592929a001");
    

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

// checkTokenExpiry("0x953dea79f6aa249e3c52b7b08c5467b097dd5422de8cb087a60163583ee092ae2486ada45e799ee53e98a6f71fb7719aa7a86b18eee4177f56e1b3159b52820f00",
//     "0x45679b79df3b91f29406fe15a19a8c04d473a0ba6b160b342cb3415c1cf910e06b3fecd0bdf8fc51c88c90916f68d982d5a1d8414592d0d72e43762ef40d3d4400", "30000");




// checkTokenExpiry("0x47adfad83202c4e3591964405c8ccdaaf449443a3c02177d53f5b3351014624203156c4cfe0f94880ed0942c9100c754c1b841888ce5d5fc2a623790728072db01", 
//     "0x0998d949f2147f9cb61fcfb097f830a878de056833b57c66ad7d58810118a6855f8205fad664a2fe671daa83bfc0beaa049a52ff7f9d8467ea40c390592929a001", "30");

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

// isValidator("0x405df3b9b5c185287b86aed199d9b8828ea188a04e7751dbcba3299f56a63ecd2815834797e74a879ffd7a466c06b7ffdc68194294df8bc610f376909dbf081b01");


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

// checkIfDeployed(contractAddress);


async function watchValidatorProposals() {
    // console.log("Fetching past ValidatorProposed events...");

    try {
        // Step 1: Fetch past events
        const pastEvents = await contract.getPastEvents('ValidatorProposed', {
            fromBlock: 0,
            toBlock: 'latest'
        });

        for (const event of pastEvents) {
            // console.log("Past Event:");
            // console.log("   Proposed By:", event.returnValues.proposedBy);
            // console.log("   Validator:", event.returnValues.validator);
            console.log(event.returnValues.validator);
            // console.log("   Tx Hash:", event.transactionHash);
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
            const peerCount = parseInt(data.result, 16); // Convert hex to decimal
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

// getPeerCount();

// proposeValidatorVote("0x8ec1a623566117361454b0ef2b676115ef12991b", true);

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

// getValidatorsByBlockNumber();


// checkIfDeployed(contractAddress)

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