const { Web3 } = require("web3"); 
const web3 = new Web3('http://127.0.0.1:8545');

// Contract setup
const contractABI = [ /* your ABI here (just ValidatorProposed part is enough) */ ];
const contractAddress = "0x2bD48779860D87F4111A59274F16a142D9519102";
const contract = new web3.eth.Contract(contractABI, contractAddress);

// Listening for ValidatorProposed event
contract.events.ValidatorProposed({
    fromBlock: 'latest' // or a specific block number
})
.on('data', (event) => {
    console.log("ValidatorProposed event received!");
    console.log("Proposed By:", event.returnValues.proposedBy);
    console.log("Validator:", event.returnValues.validator);
})
.on('error', (err) => {
    console.error("Error while listening for ValidatorProposed:", err);
});