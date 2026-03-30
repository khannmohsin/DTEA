// interact.js
// Run: node interact.js help

const { Web3 } = require("web3");
const path = require("path");
const fs = require("fs");
const fetch = require("node-fetch");

// ---------- FIXED SETTINGS ----------
const rootPath = path.resolve(__dirname, "");
const contractJson = require(path.join(rootPath, "data/NodeRegistry.json")); // Load ABI
const rpcURL = process.env.BESU_RPC_URL || "http://127.0.0.1:8545";
const web3 = new Web3(rpcURL);

function findProjectRoot(startPath) {
  let current = path.resolve(startPath);
  while (true) {
    if (fs.existsSync(path.join(current, ".git"))) return current;
    const parent = path.dirname(current);
    if (parent === current) return path.resolve(startPath, "..");
    current = parent;
  }
}

const projectRoot = findProjectRoot(rootPath);
const resultsDir = path.join(projectRoot, "results");
fs.mkdirSync(resultsDir, { recursive: true });
const gasLogPath = path.join(resultsDir, "gas_log.jsonl");

// Resolve deployed address from Truffle-style artifact
const networks = contractJson.networks || {};
const networkId = Object.keys(networks)[0];
if (!networkId) {
  console.error("No networks{} found in data/NodeRegistry.json");
  process.exit(1);
}
const contractAddress = networks[networkId].address;
if (!contractAddress) {
  console.error("No contract address in data/NodeRegistry.json networks[]");
  process.exit(1);
}

// Load prefunded keys (fixed source)
const accountsData = JSON.parse(fs.readFileSync(path.join(rootPath, "prefunded_keys.json")));
const PREFUNDED = accountsData.prefunded_accounts || [];
const DEFAULT_FROM_IDX = Number(process.env.FROM_IDX || 0);
// printf("Using FROM_IDX=%d, account=%s\n", DEFAULT_FROM_IDX, PREFUNDED[DEFAULT_FROM_IDX]?.address || "undefined");
if (!PREFUNDED[DEFAULT_FROM_IDX]) {
  console.error(`No prefunded account at index FROM_IDX=${DEFAULT_FROM_IDX}`);
  process.exit(1);
}


function walletAt(idx) {
  const w = PREFUNDED[idx];
  if (!w) throw new Error(`No prefunded account at index ${idx}`);
  return { address: w.address, privateKey: w.private_key };
}
let { address: account, privateKey } = walletAt(DEFAULT_FROM_IDX);

// Contract instance
const contract = new web3.eth.Contract(contractJson.abi, contractAddress);

// ---------- ENUMS / CONSTANTS ----------
const ROLE = { Unknown:0, Cloud:1, Fog:2, Edge:3, Sensor:4, Actuator:5 };
const OP   = { READ:1<<0, WRITE:1<<1, UPDATE:1<<2, REMOVE:1<<3 };
const ZERO32 = "0x" + "00".repeat(32);


// ---------- TX HELPER ----------

// --- add near the other event/utils helpers ---
async function msigApprovedEvents(fromBlock = 0) {
  const evs = await contract.getPastEvents('MsigApproved', {
    fromBlock: Number(fromBlock),
    toBlock: 'latest'
  });

  console.log(JSON.stringify(
    evs.map(e => ({
      blockNumber: e.blockNumber,
      tx: e.transactionHash,
      approver: e.returnValues.approver,
      approvals: Number(e.returnValues.approvals),
      actionKey: e.returnValues.actionKey
    })), 
    jsonReplacer,  // <-- use replacer here
    2
  ));
}

async function msigClearedEvents(fromBlock = 0) {
  const evs = await contract.getPastEvents('MsigCleared', { fromBlock: Number(fromBlock), toBlock: 'latest' });
  console.log(JSON.stringify(evs.map(e => ({
    blockNumber: e.blockNumber,
    tx: e.transactionHash,
    actionKey: e.returnValues.actionKey
  })), null, 2));
}

function appendJsonLine(filePath, payload) {
  fs.appendFileSync(filePath, `${JSON.stringify(payload)}\n`, "utf8");
}

function appendGasLog(functionName, receipt) {
  if (!receipt || !functionName) return;
  appendJsonLine(gasLogPath, {
    function: functionName,
    timestamp: new Date().toISOString(),
    blockNumber: Number(receipt.blockNumber || 0),
    gasUsed: Number(receipt.gasUsed || 0),
    transactionHash: receipt.transactionHash || null,
  });
}

async function sendTx(method, gas = 3_000_000, value = "0", gasLabel = null) {
  const data = method.encodeABI();
  const nonce = await web3.eth.getTransactionCount(account, "pending");
  const tx = {
    from: account,
    to: contractAddress,
    data,
    gas,
    gasPrice: "0",
    nonce,
    value
  };
  const signed = await web3.eth.accounts.signTransaction(tx, privateKey);
  const receipt = await web3.eth.sendSignedTransaction(signed.rawTransaction);
  if (gasLabel) appendGasLog(gasLabel, receipt);
  return receipt;
}

function jsonReplacer(key, value) {
  return typeof value === 'bigint' ? value.toString() : value;
}

// ---------- SMALL UTILS ----------
function parseOps(csvOrMask) {
  if (csvOrMask === undefined) throw new Error("opsCsvOrMask is required");
  if (/^\d+$/.test(csvOrMask)) return parseInt(csvOrMask, 10);
  const toOne = (s) => s.replace(/\s/g, "").toUpperCase();
  const parts = csvOrMask.split(/[|,]/).map(s => toOne(s)).filter(Boolean);
  let mask = 0;
  for (const p of parts) {
    if (!(p in OP)) throw new Error(`Unknown op: ${p}`);
    mask |= OP[p];
  }
  return mask;
}

function toBytes32(hexOrStr) {
  if (!hexOrStr) return ZERO32;
  if (hexOrStr.startsWith("0x")) {
    const clean = hexOrStr.slice(2);
    if (clean.length > 64) throw new Error("bytes32 too long");
    return "0x" + clean.padStart(64, "0");
  }
  return web3.utils.keccak256(hexOrStr);
}

function tsNowPlus(seconds) {
  const now = Math.floor(Date.now()/1000);
  return now + Number(seconds || 0);
}

async function nextPolicyId() {
  const id = await contract.methods.nextPolicyId().call();
  console.log(Number(id));
}

// =========================================================
//                       ADMIN / POLICY
// =========================================================

async function policyAdmin() {
  const admin = await contract.methods.policyAdmin().call();
  console.log(admin);
}

async function setPolicyAdmin(newAdmin) {
  const rc = await sendTx(contract.methods.setPolicyAdmin(newAdmin));
  console.log("✅ setPolicyAdmin:", rc.transactionHash);
}

async function createPolicy(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStrOrHex) {
  const fromRole = ROLE[fromRoleName] ?? Number(fromRoleName);
  const toRole   = ROLE[toRoleName]   ?? Number(toRoleName);
  const ops      = parseOps(opsCsvOrMask);
  const schema   = toBytes32(ctxSchemaStrOrHex || ZERO32);
  console.log(`Creating policy: ${fromRoleName}(${fromRole}) -> ${toRoleName}(${toRole}), ops=${ops}, ctxSchema=${schema}`);
  const rc = await sendTx(contract.methods.createPolicy(fromRole, toRole, ops, schema));
  console.log("✅ createPolicy:", rc.transactionHash);
}

async function updatePolicy(policyId, opsCsvOrMask, ctxSchemaStrOrHex) {
  const ops    = parseOps(opsCsvOrMask);
  const schema = toBytes32(ctxSchemaStrOrHex || ZERO32);
  const rc = await sendTx(contract.methods.updatePolicy(Number(policyId), ops, schema));
  console.log("✅ updatePolicy:", rc.transactionHash);
}

async function deprecatePolicy(policyId) {
  const rc = await sendTx(contract.methods.deprecatePolicy(Number(policyId)));
  console.log("✅ deprecatePolicy:", rc.transactionHash);
}

async function getPolicy(policyId) {
  const p = await contract.methods.getPolicy(Number(policyId)).call();
  console.log(JSON.stringify(p, jsonReplacer, 2));
}

// =========================================================
//                       MULTISIG (NEW)
// =========================================================
async function msigMode(requiredBool) {
  const req = (String(requiredBool).toLowerCase() === "true" || requiredBool === "1");
  const rc = await sendTx(contract.methods.setMsigMode(req));
  console.log("✅ setMsigMode:", req, rc.transactionHash);
}

async function msigAdd(approverAddr) {
  const rc = await sendTx(contract.methods.addMsigApprover(approverAddr));
  console.log("✅ addMsigApprover:", approverAddr, rc.transactionHash);
}

async function msigRemove(approverAddr) {
  const rc = await sendTx(contract.methods.removeMsigApprover(approverAddr));
  console.log("✅ removeMsigApprover:", approverAddr, rc.transactionHash);
}

async function msigThreshold(k) {
  const rc = await sendTx(contract.methods.setMsigThreshold(Number(k)));
  console.log("✅ setMsigThreshold:", k, rc.transactionHash);
}

async function msigInfo() {
  const required  = await contract.methods.msigRequired().call();
  const count     = await contract.methods.msigApproverCount().call();
  const threshold = await contract.methods.msigThreshold().call();
  console.log(JSON.stringify({
    msigRequired: required,
    msigApproverCount: Number(count),
    msigThreshold: Number(threshold),
    defaultFrom: { index: DEFAULT_FROM_IDX, account }
  }, null, 2));
}

async function msigIsApprover(addr) {
  const ok = await contract.methods.msigApprover(addr).call();
  console.log(ok);
}

async function approveCreatePolicy(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStrOrHex) {
  const fromRole = ROLE[fromRoleName] ?? Number(fromRoleName);
  const toRole   = ROLE[toRoleName]   ?? Number(toRoleName);
  const ops      = parseOps(opsCsvOrMask);
  const schema   = toBytes32(ctxSchemaStrOrHex || ZERO32);
  const rc = await sendTx(contract.methods.approveCreatePolicy(fromRole, toRole, ops, schema), 3_000_000, "0", "approvePolicy");
  console.log("✅ approveCreatePolicy:", rc.transactionHash);
}

async function approvePolicy(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStrOrHex) {
  await approveCreatePolicy(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStrOrHex);
}

async function _findPolicyId(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStr) {
  const fromRole = ROLE[fromRoleName] ?? Number(fromRoleName);
  const toRole   = ROLE[toRoleName]   ?? Number(toRoleName);
  const opsMask  = parseOps(opsCsvOrMask);
  const ctxHash  = web3.utils.keccak256(ctxSchemaStr);

  const nextId = Number(await contract.methods.nextPolicyId().call());
  if (!nextId || nextId <= 1) return 0;

  for (let id = 1; id < nextId; id++) {
    const p = await contract.methods.getPolicy(id).call();
    const versionOk     = Number(p.version) > 0;
    const notDeprecated = !p.isDeprecated;
    const rolesOk       = Number(p.fromRole) === Number(fromRole) &&
                          Number(p.toRole)   === Number(toRole);
    const opsOk         = Number(p.opsAllowed) === Number(opsMask);
    const ctxOk         = String(p.ctxSchema).toLowerCase() === String(ctxHash).toLowerCase();
    if (versionOk && notDeprecated && rolesOk && opsOk && ctxOk) {
      return id;                 // <--- RETURN (no console.log)
    }
  }
  return 0;                      // <--- RETURN (no console.log)
}

async function ensurePolicy(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStrOrHex) {
  const existingId = await _findPolicyId(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStrOrHex || "");
  if (Number(existingId || 0) > 0) {
    console.log(`exists:${Number(existingId)}`);
    return;
  }

  const fromRole = ROLE[fromRoleName] ?? Number(fromRoleName);
  const toRole   = ROLE[toRoleName]   ?? Number(toRoleName);
  const ops      = parseOps(opsCsvOrMask);
  const schema   = toBytes32(ctxSchemaStrOrHex || ZERO32);
  const rc = await sendTx(contract.methods.createPolicy(fromRole, toRole, ops, schema), 3_000_000, "0", "ensurePolicy");
  console.log(`created:${rc.transactionHash}`);
}


// =========================================================
/*                    NODES (PACKED)                       */
// =========================================================
/**
 * Calls registerNodePacked(bytes) by ABI-encoding:
 * (string nodeId, string nodeName, string nodeTypeStr, string publicKey,
 *  address registeredByAddr, string rpcURL, string registeredByNodeTypeStr, string nodeSignature)
 */
async function registerNode(nodeId, nodeName, nodeTypeStr, publicKey, registeredByAddr, rpcURL, registeredByNodeTypeStr, nodeSignature) {
  const payload = web3.eth.abi.encodeParameters(
    ["string","string","string","string","address","string","string","string"],
    [ nodeId,  nodeName,  nodeTypeStr,  publicKey,  registeredByAddr,  rpcURL,  registeredByNodeTypeStr,  nodeSignature ]
  );
  const rc = await sendTx(contract.methods.registerNodePacked(payload), 3_000_000, "0", "registerNode");
  console.log("✅ registerNodePacked:", rc.transactionHash);
}

async function isNodeRegistered(nodeSignature) {
  const ok = await contract.methods.isNodeRegistered(nodeSignature).call();
  console.log(ok);
}

async function getNodeDetailsBySignature(nodeSignature) {
  const r = await contract.methods.getNodeDetailsBySignature(nodeSignature).call();
  const details = {
    nodeId: r[0], nodeName: r[1], nodeType: Number(r[2]),
    publicKey: r[3], isRegistered: r[4], registeredBy: r[5],
    nodeSignature: r[6], registeredByNodeType: Number(r[7]),
  };
  console.log(JSON.stringify(details, null, 2));
}

async function getNodeDetailsByAddress(addr) {
  const r = await contract.methods.getNodeDetailsByAddress(addr).call();
  const details = {
    nodeId: r[0], nodeName: r[1], nodeType: Number(r[2]),
    publicKey: r[3], isRegistered: r[4], registeredBy: r[5],
    nodeSignature: r[6], registeredByNodeType: Number(r[7]),
  };
  console.log(JSON.stringify(details, null, 2));
}

async function proposeValidator(validatorAddr) {
  const rc = await sendTx(contract.methods.proposeValidator(validatorAddr));
  console.log("✅ proposeValidator:", rc.transactionHash);
}

async function isValidator(nodeSignature) {
  try {
    const ok = await contract.methods.isValidator(nodeSignature).call();
    console.log(ok);
  } catch {
    console.log(false);
  }
}

// =========================================================
//                           GRANTS
// =========================================================


async function issueGrant(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs) {
  const ops = parseOps(opsCsvOrMask);
  const exp = /^\d+$/.test(expiresAtTs) ? Number(expiresAtTs) : tsNowPlus(Number(expiresAtTs || 0));
  const rc = await sendTx(contract.methods.issueGrant(fromNodeSig, toNodeSig, Number(policyId), ops, exp), 3_000_000, "0", "issueToken");
  console.log("✅ issueGrant:", rc.transactionHash);
}

async function issueGrantDelegable(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs, delegationAllowed, delegationDepth) {
  const ops = parseOps(opsCsvOrMask);
  const exp = /^\d+$/.test(expiresAtTs) ? Number(expiresAtTs) : tsNowPlus(Number(expiresAtTs || 0));
  const allow = (String(delegationAllowed).toLowerCase() === "true" || delegationAllowed === "1");
  const depth = Number(delegationDepth || 0);
  const rc = await sendTx(
    contract.methods.issueGrantDelegable(fromNodeSig, toNodeSig, Number(policyId), ops, exp, allow, depth),
    3_000_000,
    "0",
    "issueTokenDelegable"
  );
  console.log("✅ issueGrantDelegable:", rc.transactionHash);
}

async function revokeGrant(fromNodeSig, toNodeSig, policyId) {
  const rc = await sendTx(contract.methods.revokeGrant(fromNodeSig, toNodeSig, Number(policyId)), 3_000_000, "0", "revokeToken");
  console.log("✅ revokeGrant:", rc.transactionHash);
}

async function issueToken(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs, maxDelegationDepth = 0) {
  const depth = Number(maxDelegationDepth || 0);
  if (depth > 0) {
    await issueGrantDelegable(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs, true, depth);
    return;
  }
  await issueGrant(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs);
}

async function issueTokenDelegable(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs, delegationAllowed = true, delegationDepth = 1) {
  await issueGrantDelegable(fromNodeSig, toNodeSig, policyId, opsCsvOrMask, expiresAtTs, delegationAllowed, delegationDepth);
}

async function revokeToken(fromNodeSig, toNodeSig, policyId) {
  await revokeGrant(fromNodeSig, toNodeSig, policyId);
}

async function findPolicyId(fromRoleName, toRoleName, opsCsvOrMask, ctxSchemaStr) {
  const ops = parseOps(opsCsvOrMask);                  // you already have parseOps
  const ctxHash = web3.utils.keccak256(ctxSchemaStr);  // bytes32(ctx)

  // Adjust the method name/signature if your contract differs:
  // e.g., findPolicyId(string fromRole, string toRole, uint8 ops, bytes32 ctx)
  const id = await contract.methods.findPolicyId(fromRoleName, toRoleName, ops, ctxHash).call();
  console.log(Number(id)); // stdout plain integer for Python to parse
}

async function getGrant(fromNodeSig, toNodeSig, policyId) {
  const g = await contract.methods.getGrant(fromNodeSig, toNodeSig, Number(policyId)).call();
  const grant = {
    policyId: Number(g[0]),
    opsSubset: Number(g[1]),
    issuedAt: Number(g[2]),
    expiresAt: Number(g[3]),
    isIssued: g[4],
    isRevoked: g[5],
  };
  console.log(JSON.stringify(grant, null, 2));
}

// NEW: extended view including delegation flags
async function getGrantEx(fromNodeSig, toNodeSig, policyId) {
  const g = await contract.methods.getGrantEx(fromNodeSig, toNodeSig, Number(policyId)).call();
  const grant = {
    policyId: Number(g[0]),
    opsSubset: Number(g[1]),
    issuedAt: Number(g[2]),
    expiresAt: Number(g[3]),
    isIssued: g[4],
    isRevoked: g[5],
    delegationAllowed: g[6],
    delegationDepth: Number(g[7]),
    depthDel: Number(g[8] || g[7] || 0),
    parentTokenId: g[9] || ZERO32,
  };
  console.log(JSON.stringify(grant, null, 2));
}

async function checkGrant(fromNodeSig, toNodeSig, policyId, opCsvOrMask) {
  const opBit = parseOps(opCsvOrMask);
  // eth_call is read-only, so this path consumes zero gas and is intentionally not logged.
  const ok = await contract.methods.checkGrant(fromNodeSig, toNodeSig, Number(policyId), opBit).call();
  console.log(ok);
}

async function checkGrantAndLog(fromNodeSig, toNodeSig, policyId, opCsvOrMask) {
  const opBit = parseOps(opCsvOrMask);
  const rc = await sendTx(contract.methods.checkGrantAndLog(fromNodeSig, toNodeSig, Number(policyId), opBit));
  console.log(JSON.stringify({
    granted: !!rc?.events?.AccessGranted,
    transactionHash: rc.transactionHash,
    blockNumber: Number(rc.blockNumber || 0)
  }, null, 2));
}

async function isGrantExpired(fromNodeSig, toNodeSig, policyId) {
  const ok = await contract.methods.isGrantExpired(fromNodeSig, toNodeSig, Number(policyId)).call();
  console.log(ok);
}

async function delegateGrant(currentFromSig, toSig, newFromSig, policyId, opsCsvOrMask, expiresAtTs) {
  const ops = parseOps(opsCsvOrMask);
  const exp = /^\d+$/.test(expiresAtTs) ? Number(expiresAtTs) : tsNowPlus(Number(expiresAtTs || 0));
  const rc = await sendTx(
    contract.methods.delegateGrant(currentFromSig, toSig, newFromSig, Number(policyId), ops, exp)
  );
  console.log("✅ delegateGrant:", rc.transactionHash);
}

// =========================================================
//                     BESU UTILS / EVENTS
// =========================================================

// Listen for recent ValidatorProposed events and print candidate addresses.
// Orchestrator regex-scans stdout for 0x...40 addresses.
async function listenForValidatorProposals(fromBlockHint) {
  // v4 returns BigInt → cast to Number before doing math
  const latest = Number(await web3.eth.getBlockNumber());
  const from   = Math.max(0, Number(fromBlockHint ?? (latest - 64)));

  const evs = await contract.getPastEvents('ValidatorProposed', {
    fromBlock: from,
    toBlock: 'latest'
  });

  // event ValidatorProposed(address indexed proposedBy, address indexed validator);
  const addrs = [...new Set(evs.map(e =>
    e.returnValues.validator || e.returnValues["1"]
  ).filter(Boolean))];

  console.log(addrs.join("\n"));
}


async function qbft_getValidatorsByBlockNumber(url = rpcURL) {
  const payload = { jsonrpc:"2.0", method:"qbft_getValidatorsByBlockNumber", params:["latest"], id:1 };
  const res = await fetch(url, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) });
  const data = await res.json();
  console.log(data.result || data);
}

async function proposeValidatorVote(validatorAddress, add) {
  const payload = {
    jsonrpc: "2.0",
    method: "qbft_proposeValidatorVote",
    params: [validatorAddress, add],  // add = true to add, false to remove
    id: 1
  };
  const res = await fetch(rpcURL, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });
  console.log("Vote submitted:", await res.json());
}

async function net_peerCount(url = rpcURL) {
  const payload = { jsonrpc:"2.0", method:"net_peerCount", params:[], id:1 };
  const res = await fetch(url, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) });
  const data = await res.json();
  const n = data.result ? parseInt(data.result, 16) : 0;
  console.log(n);
}

async function checkIfDeployed(addr = contractAddress) {
  const code = await web3.eth.getCode(addr);
  console.log(code !== "0x" && code !== "0x0");
}

async function whoami() {
  const idx = Number(process.env.FROM_IDX || 0);
  const { address: from } = walletAt(idx);  // from prefunded_keys.json
  const bal = await web3.eth.getBalance(from);
  console.log(JSON.stringify({
    fromIndex: idx,
    from,
    balanceWei: bal.toString()   // convert BigInt → string
  }, null, 2));
}

// =========================================================
//                           CLI
// =========================================================
async function main() {
  const [cmd, ...args] = process.argv.slice(2);

  try {
    switch (cmd) {
      case "help":
        console.log(`
RPC: ${rpcURL}
Contract: ${contractAddress}
From (PREFUNDED[FROM_IDX]): ${account}  (FROM_IDX=${DEFAULT_FROM_IDX})

Set signer per command via env var:
  FROM_IDX=2 node interact.js <command> ...

Roles: Cloud,Fog,Edge,Sensor,Actuator
Ops: READ,WRITE,UPDATE,REMOVE (or numeric mask, or "READ|REMOVE")

Admin/Policy:
  node interact.js setPolicyAdmin <newAdmin>
  node interact.js createPolicy <fromRole> <toRole> <opsCsv|mask> [ctxSchemaStrOrHex]
  node interact.js updatePolicy <policyId> <opsCsv|mask> [ctxSchemaStrOrHex]
  node interact.js deprecatePolicy <policyId>
  node interact.js getPolicy <policyId>
  node interact.js ensurePolicy <fromRole> <toRole> <opsCsv|mask> [ctxSchemaStrOrHex]


Multisig:
  node interact.js msigMode <true|false>
  node interact.js msigAdd <approverAddr>
  node interact.js msigRemove <approverAddr>
  node interact.js msigThreshold <k>
  node interact.js msigInfo
  node interact.js msigIsApprover <addr>
  node interact.js approveCreatePolicy <fromRole> <toRole> <opsCsv|mask> [ctxSchemaStrOrHex]
  node interact.js approvePolicy <fromRole> <toRole> <opsCsv|mask> [ctxSchemaStrOrHex]

Nodes (packed register):
  node interact.js registerNode <nodeId> <nodeName> <nodeTypeStr> <publicKey> <registeredByAddr> <rpcURL> <registeredByNodeTypeStr> <nodeSignature>
  node interact.js isNodeRegistered <nodeSignature>
  node interact.js getNodeBySig <nodeSignature>
  node interact.js getNodeByAddr <address>
  node interact.js proposeValidator <validatorAddr>
  node interact.js isValidator <nodeSignature>

Grants:
  node interact.js issueGrant <fromSig> <toSig> <policyId> <opsCsv|mask> <expiresAtTs or +seconds>
  node interact.js issueGrantDelegable <fromSig> <toSig> <policyId> <opsCsv|mask> <expiresAtTs or +seconds> <delegationAllowed:true|false> <delegationDepth>
  node interact.js issueToken <fromSig> <toSig> <policyId> <opsCsv|mask> <expiresAtTs or +seconds> [maxDelegationDepth]
  node interact.js issueTokenDelegable <fromSig> <toSig> <policyId> <opsCsv|mask> <expiresAtTs or +seconds> [delegationAllowed:true|false] [delegationDepth]
  node interact.js delegateGrant <currentFromSig> <toSig> <newFromSig> <policyId> <opsCsv|mask> <expiresAtTs or +seconds>
  node interact.js revokeGrant <fromSig> <toSig> <policyId>
  node interact.js revokeToken <fromSig> <toSig> <policyId>
  node interact.js getGrant <fromSig> <toSig> <policyId>
  node interact.js getGrantEx <fromSig> <toSig> <policyId>
  node interact.js checkGrant <fromSig> <toSig> <policyId> <opCsv|mask>
  node interact.js checkGrantAndLog <fromSig> <toSig> <policyId> <opCsv|mask>
  node interact.js isGrantExpired <fromSig> <toSig> <policyId>

Besu/Utils:
  node interact.js qbft_getValidators
  node interact.js peerCount
  node interact.js checkIfDeployed
  node interact.js nextPolicyId
  node interact.js whoami
`); break;
      case "nextPolicyId": await nextPolicyId(); break;
      // Admin/Policy
      case "policyAdmin":   await policyAdmin(); break;
      case "setPolicyAdmin": await setPolicyAdmin(args[0]); break;
      case "createPolicy":   await createPolicy(args[0], args[1], args[2], args[3]); break;
      case "updatePolicy":   await updatePolicy(args[0], args[1], args[2]); break;
      case "deprecatePolicy":await deprecatePolicy(args[0]); break;
      case "getPolicy":      await getPolicy(args[0]); break;
      case "ensurePolicy": await ensurePolicy(args[0], args[1], args[2], args[3]); break;

      // Multisig
      case "msigMode":       await msigMode(args[0]); break;
      case "msigAdd":        await msigAdd(args[0]); break;
      case "msigRemove":     await msigRemove(args[0]); break;
      case "msigThreshold":  await msigThreshold(args[0]); break;
      case "msigInfo":       await msigInfo(); break;
      case "msigIsApprover": await msigIsApprover(args[0]); break;
      case "approveCreatePolicy": await approveCreatePolicy(args[0], args[1], args[2], args[3]); break;
      case "approvePolicy": await approvePolicy(args[0], args[1], args[2], args[3]); break;

      // Nodes (packed)
      case "registerNode":     await registerNode(...args); break;
      case "isNodeRegistered": await isNodeRegistered(args[0]); break;
      case "getNodeBySig":     await getNodeDetailsBySignature(args[0]); break;
      case "getNodeByAddr":    await getNodeDetailsByAddress(args[0]); break;
      case "proposeValidator": await proposeValidator(args[0]); break;
      case "proposeValidatorVote": await proposeValidatorVote(args[0], args[1]); break;
      case "isValidator":      await isValidator(args[0]); break;

      // Grants
      case "issueGrant":     await issueGrant(args[0], args[1], args[2], args[3], args[4]); break;
      case "issueToken":     await issueToken(args[0], args[1], args[2], args[3], args[4], args[5]); break;
      case "revokeGrant":    await revokeGrant(args[0], args[1], args[2]); break;                 // from, to, policyId
      case "revokeToken":    await revokeToken(args[0], args[1], args[2]); break;
      case "getGrant":       await getGrant(args[0], args[1], args[2]); break;                   // from, to, policyId
      case "getGrantEx":     await getGrantEx(args[0], args[1], args[2]); break;                 // from, to, policyId
      case "checkGrant":     await checkGrant(args[0], args[1], args[2], args[3]); break;        // from, to, policyId, op
      case "checkGrantAndLog": await checkGrantAndLog(args[0], args[1], args[2], args[3]); break;
      case "isGrantExpired": await isGrantExpired(args[0], args[1], args[2]); break;             // from, to, policyId
      case "delegateGrant":  await delegateGrant(args[0], args[1], args[2], args[3], args[4], args[5]); break; // currFrom, to, newFrom, policyId, ops, exp
      case "issueGrantDelegable": await issueGrantDelegable(args[0], args[1], args[2], args[3], args[4], args[5], args[6]); break;
      case "issueTokenDelegable": await issueTokenDelegable(args[0], args[1], args[2], args[3], args[4], args[5], args[6]); break;

      case "findPolicyId": {const id = await _findPolicyId(args[0], args[1], args[2], args[3]); console.log(Number(id || 0)); break;}
      case "msigApprovedEvents": await msigApprovedEvents(args[0]); break;
      case "msigClearedEvents":  await msigClearedEvents(args[0]); break;
      // Besu/Utils
      case "listenForValidatorProposals": await listenForValidatorProposals(args[0]); break;
      case "qbft_getValidators": await qbft_getValidatorsByBlockNumber(); break;
      case "peerCount":          await net_peerCount(); break;
      case "checkIfDeployed":    await checkIfDeployed(); break;
      case "whoami":             await whoami(); break;
      case "nextPolicyId": {const np = await contract.methods.nextPolicyId().call();console.log(np.toString());break;}

      default:
        console.log("Unknown command. Run: node interact.js help");
        break;
    }
  } catch (e) {
    // Make CLI commands fail properly so bash can assert reverts.
    const msg = e?.reason || e?.message || e;
    console.error("❌ Error:", msg);
    process.exitCode = 1;   // <-- mark failure for the shell
  }
}

if (require.main === module) main();

module.exports = {
  ROLE, OP,
  // Admin/Policy
  policyAdmin, setPolicyAdmin, createPolicy, updatePolicy, deprecatePolicy, getPolicy,
  // Multisig
  msigMode, msigAdd, msigRemove, msigThreshold, msigInfo, msigIsApprover, approveCreatePolicy, approvePolicy,
  // Nodes
  registerNode, isNodeRegistered, getNodeDetailsBySignature, getNodeDetailsByAddress,
  proposeValidator, isValidator, proposeValidatorVote,
  // Grants
  issueGrant, issueGrantDelegable, issueToken, issueTokenDelegable, delegateGrant, revokeGrant, revokeToken, getGrant, getGrantEx, checkGrant, checkGrantAndLog, isGrantExpired, ensurePolicy,
  // Utils
  qbft_getValidatorsByBlockNumber, net_peerCount, checkIfDeployed, nextPolicyId, whoami, listenForValidatorProposals
};
