#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const ganache = require("ganache");
const { Web3 } = require("web3");

const repoRoot = path.resolve(__dirname, "..");
const contractRoot = path.join(repoRoot, "Node_root", "smart_contract_deployment");
const contractsDir = path.join(contractRoot, "contracts");
const buildDir = path.join(contractRoot, "build", "contracts");
const resultsDir = path.join(repoRoot, "results");
const outputPath = path.join(resultsDir, "contract_metrics.json");

const contractNames = [
  "NodeRegistry",
  "CapabilityGrant",
  "ValidatorGovernance",
  "PolicyMultisig",
];

function countNonBlankLines(source) {
  return source.split(/\r?\n/).filter((line) => line.trim().length > 0).length;
}

async function measureDeploymentGas(web3, from, artifact) {
  const deployment = new web3.eth.Contract(artifact.abi).deploy({
    data: artifact.bytecode,
  });
  const gasEstimate = Number(await deployment.estimateGas({ from }));
  let receipt = null;
  await deployment.send({
    from,
    gas: Math.max(gasEstimate + 250000, 6000000),
    maxFeePerGas: web3.utils.toWei("2", "gwei"),
    maxPriorityFeePerGas: "0",
  }).once("receipt", (rc) => {
    receipt = rc;
  });
  return {
    estimatedDeploymentGas: gasEstimate,
    deploymentGasUsed: Number(receipt?.gasUsed || 0),
  };
}

async function main() {
  execFileSync("npx", ["truffle", "compile", "--all"], {
    cwd: contractRoot,
    stdio: "inherit",
  });

  const provider = ganache.provider({
    logging: { quiet: true },
    wallet: { totalAccounts: 2, defaultBalance: 1_000 },
    chain: { chainId: 1337, networkId: 1337, hardfork: "shanghai" },
  });
  const web3 = new Web3(provider);
  const [from] = await web3.eth.getAccounts();

  const rows = [];
  let totalLines = 0;
  let totalBytecodeKb = 0;
  let totalEstimatedGas = 0;
  let totalDeploymentGas = 0;

  for (const contractName of contractNames) {
    const sourcePath = path.join(contractsDir, `${contractName}.sol`);
    const artifactPath = path.join(buildDir, `${contractName}.json`);
    const source = fs.readFileSync(sourcePath, "utf8");
    const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
    const bytecodeHex = (artifact.bytecode || "").replace(/^0x/, "");
    const bytecodeSizeKb = (bytecodeHex.length / 2) / 1024;
    const sourceLines = countNonBlankLines(source);
    const deploymentGas = await measureDeploymentGas(web3, from, artifact);

    totalLines += sourceLines;
    totalBytecodeKb += bytecodeSizeKb;
    totalEstimatedGas += deploymentGas.estimatedDeploymentGas;
    totalDeploymentGas += deploymentGas.deploymentGasUsed;

    rows.push({
      contract: contractName,
      source_lines_non_blank: sourceLines,
      bytecode_size_kb: Number(bytecodeSizeKb.toFixed(3)),
      estimated_deployment_gas: deploymentGas.estimatedDeploymentGas,
      deployment_gas_used: deploymentGas.deploymentGasUsed,
    });
  }

  rows.push({
    contract: "TOTALS",
    source_lines_non_blank: totalLines,
    bytecode_size_kb: Number(totalBytecodeKb.toFixed(3)),
    estimated_deployment_gas: totalEstimatedGas,
    deployment_gas_used: totalDeploymentGas,
  });

  fs.mkdirSync(resultsDir, { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(rows, null, 2));
  console.log(JSON.stringify(rows, null, 2));

  if (typeof provider.disconnect === "function") {
    provider.disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
