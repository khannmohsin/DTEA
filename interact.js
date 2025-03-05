const Web3 = require("web3");
const contractJson = require("./build/contracts/FogNodeRegistry.json"); // Load ABI
const web3 = new Web3("http://127.0.0.1:8545"); // Besu JSON-RPC

const contractAddress = "0xE1e11DDBe87570E345e0dDA79654Fb0f79eC9E14"; // Replace with your contract address
const account = "0x595d56ef1f9732DAc1726A6feb61aE732FF3E8fA"; // Replace with your account address
const privateKey = "1dd0945a26c326f8c2d804c47a5a1b7cdbd12d173feabb595bfe90d916c2c75d"; // ⚠️ Do not expose private key

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

getAllTransactions();

// async function checkFogNode(fogId) {
//     const isRegistered = await contract.methods.isFogNodeRegistered(fogId).call();
//     console.log(`✅ Is Fog Node ${fogId} registered? ${isRegistered}`);
// }

// checkFogNode("fog-123");

// async function getTransaction(txHash) {
//     const tx = await web3.eth.getTransaction(txHash);
//     console.log("✅ Transaction Details:", tx);
// }
// async function registerFogNode(fogId, nodeName, nodeType, publicKey) {
//     const tx = contract.methods.registerFogNode(fogId, nodeName, nodeType, publicKey);
//     const gas = await tx.estimateGas({ from: account });

//     const signedTx = await web3.eth.accounts.signTransaction(
//         {
//             to: contractAddress,
//             data: tx.encodeABI(),
//             gas: gas,
//             gasPrice: "0",  // No gas cost in Besu private network
//         },
//         privateKey
//     );

//     const receipt = await web3.eth.sendSignedTransaction(signedTx.rawTransaction);
//     console.log(`✅ Fog Node Registered! Transaction Hash: ${receipt.transactionHash}`);
// }

// // Example Usage:
// registerFogNode("fog-123", "FogNode A", "Edge", "ABC123");