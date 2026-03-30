const { expect } = require("chai");
const { ethers } = require("hardhat");

const ROLE = { Unknown: 0, Cloud: 1, Fog: 2, Edge: 3, Sensor: 4, Actuator: 5 };
const OP_READ = 1 << 0;
const OP_WRITE = 1 << 1;

function buildRegPayload({
  nodeId, nodeName, nodeTypeStr, publicKey,
  registeredBy, rpcURL, registeredByNodeTypeStr, nodeSignature,
}) {
  const abi = new ethers.AbiCoder();
  return abi.encode(
    ["string", "string", "string", "string", "address", "string", "string", "string"],
    [nodeId, nodeName, nodeTypeStr, publicKey, registeredBy, rpcURL, registeredByNodeTypeStr, nodeSignature]
  );
}

describe("NodeRegistry delegation lineage", function () {
  let reg;
  let admin, fogOwner, edgeOwnerA, edgeOwnerB;

  async function nowTs() {
    const block = await ethers.provider.getBlock("latest");
    return block.timestamp;
  }

  async function registerNode(signer, nodeId, nodeName, nodeTypeStr, nodeSignature) {
    const payload = buildRegPayload({
      nodeId,
      nodeName,
      nodeTypeStr,
      publicKey: `pk-${nodeId}`,
      registeredBy: signer.address,
      rpcURL: `http://${nodeId.toLowerCase()}`,
      registeredByNodeTypeStr: "Cloud",
      nodeSignature,
    });
    await reg.connect(signer).registerNodePacked(payload);
  }

  async function seedTopology() {
    await registerNode(fogOwner, "FG-1", "Fog", "Fog", "sigFog");
    await registerNode(edgeOwnerA, "ED-1", "EdgeOne", "Edge", "sigEdgeA");
    await registerNode(edgeOwnerB, "ED-2", "EdgeTwo", "Edge", "sigEdgeB");
    await reg.connect(admin).createPolicy(ROLE.Edge, ROLE.Fog, OP_READ | OP_WRITE, ethers.ZeroHash);
  }

  beforeEach(async () => {
    [admin, fogOwner, edgeOwnerA, edgeOwnerB] = await ethers.getSigners();
    const Reg = await ethers.getContractFactory("NodeRegistry", admin);
    reg = await Reg.deploy();
    await reg.waitForDeployment();
    await seedTopology();
  });

  it("reverts when a child requests ops outside the parent grant", async () => {
    const exp = (await nowTs()) + 3600;
    await reg.connect(fogOwner).issueGrantDelegable("sigEdgeA", "sigFog", 1, OP_READ, exp, true, 2);

    await expect(
      reg.connect(edgeOwnerA).issueTokenDelegable("sigEdgeA", "sigFog", "sigEdgeB", 1, OP_READ | OP_WRITE, exp - 30, 1)
    ).to.be.revertedWithCustomError(reg, "OpsSubsetExceedsAllowed");
  });

  it("reverts when requested depth is equal to or greater than the parent depth", async () => {
    const exp = (await nowTs()) + 3600;
    await reg.connect(fogOwner).issueGrantDelegable("sigEdgeA", "sigFog", 1, OP_READ, exp, true, 2);

    await expect(
      reg.connect(edgeOwnerA).issueTokenDelegable("sigEdgeA", "sigFog", "sigEdgeB", 1, OP_READ, exp - 30, 2)
    ).to.be.revertedWithCustomError(reg, "InvalidDelegationDepth");

    await expect(
      reg.connect(edgeOwnerA).issueTokenDelegable("sigEdgeA", "sigFog", "sigEdgeB", 1, OP_READ, exp - 30, 3)
    ).to.be.revertedWithCustomError(reg, "InvalidDelegationDepth");
  });

  it("reverts when the parent token has zero delegation depth", async () => {
    const exp = (await nowTs()) + 3600;
    await reg.connect(fogOwner)["issueGrant(string,string,uint256,uint8,uint64,uint8)"]("sigEdgeA", "sigFog", 1, OP_READ, exp, 0);

    await expect(
      reg.connect(edgeOwnerA).issueTokenDelegable("sigEdgeA", "sigFog", "sigEdgeB", 1, OP_READ, exp - 30, 0)
    ).to.be.revertedWithCustomError(reg, "InvalidDelegationDepth");
  });

  it("accepts a valid subset delegation and stores the parent linkage", async () => {
    const exp = (await nowTs()) + 3600;
    await reg.connect(fogOwner).issueGrantDelegable("sigEdgeA", "sigFog", 1, OP_READ | OP_WRITE, exp, true, 2);

    await expect(
      reg.connect(edgeOwnerA).issueTokenDelegable("sigEdgeA", "sigFog", "sigEdgeB", 1, OP_READ, exp - 30, 1)
    ).to.emit(reg, "GrantDelegated");

    expect(await reg.checkGrant("sigEdgeB", "sigFog", 1, OP_READ)).to.equal(true);
    const lineage = await reg.getGrantLineage("sigEdgeB", "sigFog", 1);
    expect(lineage[0]).to.equal(1);
    expect(lineage[1]).to.not.equal(ethers.ZeroHash);
  });

  it("invalidates a child grant when the parent is revoked", async () => {
    const exp = (await nowTs()) + 3600;
    await reg.connect(fogOwner).issueGrantDelegable("sigEdgeA", "sigFog", 1, OP_READ | OP_WRITE, exp, true, 2);
    await reg.connect(edgeOwnerA).issueTokenDelegable("sigEdgeA", "sigFog", "sigEdgeB", 1, OP_READ, exp - 30, 1);

    expect(await reg.checkGrant("sigEdgeB", "sigFog", 1, OP_READ)).to.equal(true);
    await reg.connect(fogOwner).revokeGrant("sigEdgeA", "sigFog", 1);
    expect(await reg.checkGrant("sigEdgeB", "sigFog", 1, OP_READ)).to.equal(false);
  });

  it("emits access audit events for granted and denied checks", async () => {
    const exp = (await nowTs()) + 3600;
    await reg.connect(fogOwner).issueGrantDelegable("sigEdgeA", "sigFog", 1, OP_READ, exp, true, 1);

    await expect(
      reg.connect(edgeOwnerA).checkGrantAndLog("sigEdgeA", "sigFog", 1, OP_READ)
    ).to.emit(reg, "AccessGranted");

    await expect(
      reg.connect(edgeOwnerB).checkGrantAndLog("sigEdgeB", "sigFog", 1, OP_READ)
    ).to.emit(reg, "AccessDenied");
  });
});
