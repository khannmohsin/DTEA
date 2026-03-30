// SPDX-License-Identifier: MIT
pragma solidity ^0.8.4;

contract NodeRegistry {
    // -------- Ops bitmask --------
    uint8 private constant OP_READ   = 1 << 0; // 1
    uint8 private constant OP_WRITE  = 1 << 1; // 2
    uint8 private constant OP_UPDATE = 1 << 2; // 4
    uint8 private constant OP_REMOVE = 1 << 3; // 8

    // -------- Custom errors --------
    error NotPolicyAdmin();
    error NotResourceOwner();
    error InvalidRoles();
    error EmptyOpsAllowed();
    error PolicyNotFound();
    error PolicyDeprecated();
    error PolicyRoleMismatch();
    error EmptyOpsSubset();
    error InvalidExpiry();
    error GrantAlreadyActive();
    error NodeNotRegistered();
    error DuplicateSignature();
    error AddressNotRegistered();
    error ZeroAddr();
    error DuplicatePolicy();
    error DuplicateNodeId();
    error OpsSubsetExceedsAllowed();
    error NotGrantHolder();
    error AlreadyRevoked();

    // -------- Types --------
    enum NodeType { Unknown, Cloud, Fog, Edge, Sensor, Actuator }

    struct IoTNode {
        string   nodeName;
        NodeType nodeType;
        string   publicKey;
        bool     isRegistered;
        address  registeredBy;
        NodeType registeredByNodeType;
        string   nodeSignature;
    }

    struct Policy {
        NodeType fromRole;
        NodeType toRole;
        uint8    opsAllowed;
        bool     isDeprecated;
        bytes32  ctxSchema;
        bytes32  policyHash;
        uint32   version;
    }

    struct CapabilityGrant {
        uint64 issuedAt;
        uint64 expiresAt;
        uint32 policyId;
        uint8  opsSubset;
        bool   isIssued;
        bool   isRevoked;
        bool   delegationAllowed;
        uint8  delegationDepth;
        bytes32 parentGrantId; // NEW: linkage for delegated grants
    }

    // -------- Storage --------
    mapping(string => IoTNode) public iotNodes;            // nodeId => details
    mapping(string => string)  public nodeSignatureToNodeId;
    mapping(address => string) public addressToNodeId;
    mapping(address => string) public nodeRpcUrls;

    mapping(uint256 => Policy) public policies;
    uint256 public nextPolicyId;
    address public policyAdmin;

    // Keyed by keccak256(abi.encode(fromSig, toSig, policyId))
    mapping(bytes32 => CapabilityGrant) public grants;

    // policyHash -> policyId (for duplicate detection)
    mapping(bytes32 => uint256) public policyIdByHash;

    // -------- Events --------
    event ValidatorProposed(address indexed proposedBy, address indexed validator);
    event RpcUrlMapped(address indexed nodeAddress, string rpcURL);

    event NodeRegistered(
        string indexed nodeId,
        string nodeName,
        NodeType nodeType,
        string publicKey,
        address registeredBy,
        NodeType registeredByNodeType,
        string nodeSignature
    );

    event NodeOwnershipTransferred(string indexed nodeId, address indexed previousOwner, address indexed newOwner);

    event PolicyCreated(
        uint256 indexed policyId,
        NodeType fromRole,
        NodeType toRole,
        uint8 opsAllowed,
        bytes32 ctxSchema,
        uint32 version,
        bytes32 policyHash
    );
    event PolicyUpdated(
        uint256 indexed policyId,
        uint32 version,
        uint8 opsAllowed,
        bytes32 ctxSchema,
        bytes32 policyHash
    );
    event PolicyDeprecatedEvent(uint256 indexed policyId);
    event PolicyChanged(uint256 indexed policyId, uint32 version);

    event GrantIssued(bytes32 indexed grantId);
    event GrantExtended(bytes32 indexed grantId, uint64 newExpiresAt, uint8 newOpsSubset);
    event GrantRevoked(bytes32 indexed grantId);
    event GrantDelegated(bytes32 indexed parentGrantId, bytes32 indexed grantId, uint8 depthRemaining);

    // ============== MULTISIG CONFIG (STATE + EVENTS) ==============
    bool public msigRequired;                     // on/off switch
    mapping(address => bool) public msigApprover; // who can approve
    uint8  public msigApproverCount;
    uint8  public msigThreshold;                  // 1..approverCount

    // action approvals (only used for createPolicy in this version)
    mapping(bytes32 => uint256) private msigApprovalsCount;
    mapping(bytes32 => mapping(address => bool)) private msigApprovedBy;

    // nonce: changes the action key so previous approvals don't apply
    uint256 private msigNonceCreatePolicy;

    event MsigModeSet(bool required);
    event MsigApproverAdded(address indexed approver);
    event MsigApproverRemoved(address indexed approver);
    event MsigThresholdSet(uint8 threshold);
    event MsigApproved(bytes32 indexed actionKey, address indexed approver, uint256 approvals);
    event MsigCleared(bytes32 indexed actionKey);

    constructor() {
        policyAdmin = msg.sender;
    }

    // =========================================================
    //                       NODE REGISTRATION
    // =========================================================
    function registerNodePacked(bytes calldata payload) external {
        (
            string memory nodeId,
            string memory nodeName,
            string memory nodeTypeStr,
            string memory publicKey,
            address registeredBy,
            string memory rpcURL,
            string memory registeredByNodeTypeStr,
            string memory nodeSignature
        ) = abi.decode(
            payload,
            (string,string,string,string,address,string,string,string)
        );

        if (registeredBy == address(0)) revert ZeroAddr();
        // if (msg.sender != registeredBy) revert NotResourceOwner(); // Harden: only the registering owner can submit
        if (bytes(iotNodes[nodeId].nodeName).length != 0 && iotNodes[nodeId].isRegistered) revert DuplicateNodeId();
        if (bytes(nodeSignatureToNodeId[nodeSignature]).length != 0) revert DuplicateSignature();

        nodeSignatureToNodeId[nodeSignature] = nodeId;

        (NodeType nodeType, NodeType regByNodeType) = _parseRoles(nodeTypeStr, registeredByNodeTypeStr);

        _setNodeHeader(nodeId, nodeName, nodeType);
        _setNodeTail(nodeId, publicKey, registeredBy, regByNodeType, nodeSignature, rpcURL);
    }

    function transferNodeOwnership(string calldata nodeId, address newOwner) external {
        if (newOwner == address(0)) revert ZeroAddr();
        if (!iotNodes[nodeId].isRegistered) revert NodeNotRegistered();
        address prev = iotNodes[nodeId].registeredBy;
        // if (msg.sender != prev) revert NotResourceOwner();

        // update owner
        iotNodes[nodeId].registeredBy = newOwner;

        // clear old reverse index and set new one
        addressToNodeId[prev] = "";
        addressToNodeId[newOwner] = nodeId;

        // carry over rpcURL mapping (if any) to new owner address
        string memory url = nodeRpcUrls[prev];
        if (bytes(url).length != 0) {
            nodeRpcUrls[newOwner] = url;
            emit RpcUrlMapped(newOwner, url);
        }

        emit NodeOwnershipTransferred(nodeId, prev, newOwner);
    }

    function isNodeRegistered(string calldata nodeSignature) external view returns (bool) {
        string memory nodeId = nodeSignatureToNodeId[nodeSignature];
        if (bytes(nodeId).length == 0) return false;
        if (!iotNodes[nodeId].isRegistered) return false;
        return keccak256(abi.encodePacked(iotNodes[nodeId].nodeSignature))
            == keccak256(abi.encodePacked(nodeSignature));
    }

    function proposeValidator(address validator) external {
        if (validator == address(0)) revert ZeroAddr();
        emit ValidatorProposed(msg.sender, validator);
    }

    function _parseRoles(
        string memory nodeTypeStr,
        string memory registeredByNodeTypeStr
    ) private pure returns (NodeType nodeType, NodeType regByNodeType) {
        NodeType t1 = getNodeType(nodeTypeStr);
        if (t1 == NodeType.Unknown) revert InvalidRoles();
        NodeType t2 = getNodeType(registeredByNodeTypeStr);
        if (t2 == NodeType.Unknown) revert InvalidRoles();
        return (t1, t2);
    }

    function _setNodeHeader(
        string memory nodeId,
        string memory nodeName,
        NodeType nodeType
    ) private {
        iotNodes[nodeId].nodeName = nodeName;
        iotNodes[nodeId].nodeType = nodeType;
    }

    function _setNodeTail(
        string memory nodeId,
        string memory publicKey,
        address registeredBy,
        NodeType registeredByNodeType,
        string memory nodeSignature,
        string memory rpcURL
    ) private {
        iotNodes[nodeId].publicKey = publicKey;
        iotNodes[nodeId].isRegistered = true;
        iotNodes[nodeId].registeredBy = registeredBy;
        iotNodes[nodeId].registeredByNodeType = registeredByNodeType;
        iotNodes[nodeId].nodeSignature = nodeSignature;

        addressToNodeId[registeredBy] = nodeId;
        nodeRpcUrls[registeredBy] = rpcURL;

        emit RpcUrlMapped(registeredBy, rpcURL);
        emit NodeRegistered(
            nodeId,
            iotNodes[nodeId].nodeName,
            iotNodes[nodeId].nodeType,
            publicKey,
            registeredBy,
            registeredByNodeType,
            nodeSignature
        );
    }

    function getNodeDetailsBySignature(string calldata nodeSignature)
        external
        view
        returns (
            string memory,
            string memory,
            NodeType,
            string memory,
            bool,
            address,
            string memory,
            NodeType
        )
    {
        string memory nodeId = nodeSignatureToNodeId[nodeSignature];

        if (
            !iotNodes[nodeId].isRegistered ||
            keccak256(abi.encodePacked(iotNodes[nodeId].nodeSignature)) !=
                keccak256(abi.encodePacked(nodeSignature))
        ) revert NodeNotRegistered();

        return (
            nodeId,
            iotNodes[nodeId].nodeName,
            iotNodes[nodeId].nodeType,
            iotNodes[nodeId].publicKey,
            iotNodes[nodeId].isRegistered,
            iotNodes[nodeId].registeredBy,
            iotNodes[nodeId].nodeSignature,
            iotNodes[nodeId].registeredByNodeType
        );
    }

    function getNodeDetailsByAddress(address nodeAddress)
        external
        view
        returns (
            string memory,
            string memory,
            NodeType,
            string memory,
            bool,
            address,
            string memory,
            NodeType
        )
    {
        string memory nodeId = addressToNodeId[nodeAddress];
        if (bytes(nodeId).length == 0) revert AddressNotRegistered();

        return (
            nodeId,
            iotNodes[nodeId].nodeName,
            iotNodes[nodeId].nodeType,
            iotNodes[nodeId].publicKey,
            iotNodes[nodeId].isRegistered,
            iotNodes[nodeId].registeredBy,
            iotNodes[nodeId].nodeSignature,
            iotNodes[nodeId].registeredByNodeType
        );
    }

    // =========================================================
    //                         POLICY REGISTRY
    // =========================================================
    modifier onlyPolicyAdmin() {
        if (msg.sender != policyAdmin) revert NotPolicyAdmin();
        _;
    }

    function setPolicyAdmin(address newAdmin) external onlyPolicyAdmin {
        if (newAdmin == address(0)) revert ZeroAddr();
        policyAdmin = newAdmin;
    }

    // ---------- Multisig config (admin) ----------
    function setMsigMode(bool required) external onlyPolicyAdmin {
        msigRequired = required;
        emit MsigModeSet(required);
    }

    function addMsigApprover(address a) external onlyPolicyAdmin {
        if (a == address(0) || msigApprover[a]) revert NotPolicyAdmin(); // reuse admin guard; bad arg treated as admin error
        msigApprover[a] = true;
        unchecked { msigApproverCount += 1; }
        if (msigThreshold == 0) msigThreshold = 1;
        emit MsigApproverAdded(a);
    }

    function removeMsigApprover(address a) external onlyPolicyAdmin {
        if (!msigApprover[a]) revert NotPolicyAdmin();
        msigApprover[a] = false;
        unchecked { msigApproverCount -= 1; }
        if (msigApproverCount > 0 && msigThreshold > msigApproverCount) {
            msigThreshold = msigApproverCount;
            emit MsigThresholdSet(msigThreshold);
        }
        emit MsigApproverRemoved(a);
    }

    function setMsigThreshold(uint8 k) external onlyPolicyAdmin {
        if (k == 0 || k > msigApproverCount) revert InvalidRoles(); // minimal guard
        msigThreshold = k;
        emit MsigThresholdSet(k);
    }

    // ---------- Multisig: approval + gate for createPolicy ----------
    function _keyCreatePolicy(uint8 fromRole, uint8 toRole, uint8 ops, bytes32 schema)
        internal view returns (bytes32)
    {
        // include a domain tag + current nonce so approvals are one‑shot
        return keccak256(
            abi.encodePacked(
                "CREATE_POLICY",
                msigNonceCreatePolicy,
                fromRole,
                toRole,
                ops,
                schema
            )
        );
    }

    function approveCreatePolicy(uint8 fromRole, uint8 toRole, uint8 ops, bytes32 schema) external {
        if (!msigApprover[msg.sender]) revert NotPolicyAdmin();
        bytes32 k = _keyCreatePolicy(fromRole, toRole, ops, schema);
        if (msigApprovedBy[k][msg.sender]) revert GrantAlreadyActive(); // already approved by this approver
        msigApprovedBy[k][msg.sender] = true;
        uint256 c = msigApprovalsCount[k] + 1;
        msigApprovalsCount[k] = c;
        emit MsigApproved(k, msg.sender, c);
    }

    function _requireAndClearApprovals(bytes32 k) internal {
        if (!msigRequired) return;
        if (msigThreshold == 0 || msigApproverCount == 0) revert PolicyNotFound(); // generic guard
        if (msigApprovalsCount[k] < msigThreshold) revert PolicyNotFound();
        // consume approvals by bumping the nonce; old approvals won't match new key
        unchecked { msigNonceCreatePolicy += 1; }
        // optional: clear counter for tidiness
        msigApprovalsCount[k] = 0;
        emit MsigCleared(k);
    }

    function _computePolicyHash(
        NodeType fromRole,
        NodeType toRole,
        uint8 opsAllowed,
        bytes32 ctxSchema
    ) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(fromRole, toRole, opsAllowed, ctxSchema));
    }

    function createPolicy(
        NodeType fromRole,
        NodeType toRole,
        uint8 opsAllowed,
        bytes32 ctxSchema
    ) external onlyPolicyAdmin returns (uint256 policyId) {
        _requireAndClearApprovals(_keyCreatePolicy(uint8(fromRole), uint8(toRole), opsAllowed, ctxSchema));

        if (fromRole == NodeType.Unknown || toRole == NodeType.Unknown) revert InvalidRoles();
        if (opsAllowed == 0) revert EmptyOpsAllowed();

        // compute canonical hash and reject duplicates
        bytes32 h = _computePolicyHash(fromRole, toRole, opsAllowed, ctxSchema);
        uint256 existing = policyIdByHash[h];
        if (existing != 0 && !policies[existing].isDeprecated) revert DuplicatePolicy();

        unchecked { policyId = ++nextPolicyId; }

        policies[policyId].fromRole     = fromRole;
        policies[policyId].toRole       = toRole;
        policies[policyId].opsAllowed   = opsAllowed;
        policies[policyId].isDeprecated = false;
        policies[policyId].ctxSchema    = ctxSchema;
        policies[policyId].policyHash   = h;
        policies[policyId].version      = 1;

        // index it for fast duplicate checks
        policyIdByHash[h] = policyId;

        emit PolicyCreated(policyId, fromRole, toRole, opsAllowed, ctxSchema, 1, h);
        emit PolicyChanged(policyId, 1);
    }

    function updatePolicy(uint256 policyId, uint8 opsAllowed, bytes32 ctxSchema) external onlyPolicyAdmin {
        if (policies[policyId].version == 0) revert PolicyNotFound();
        if (policies[policyId].isDeprecated) revert PolicyDeprecated();
        if (opsAllowed == 0) revert EmptyOpsAllowed();

        // compute new hash and check it doesn't collide with another live policy
        bytes32 newH = _computePolicyHash(policies[policyId].fromRole, policies[policyId].toRole, opsAllowed, ctxSchema);
        uint256 existing = policyIdByHash[newH];
        if (existing != 0 && existing != policyId && !policies[existing].isDeprecated) revert DuplicatePolicy();

        // clear old index and set new
        policyIdByHash[policies[policyId].policyHash] = 0;

        policies[policyId].opsAllowed = opsAllowed;
        policies[policyId].ctxSchema  = ctxSchema;
        unchecked { policies[policyId].version += 1; }
        policies[policyId].policyHash = newH;

        policyIdByHash[newH] = policyId;

        emit PolicyUpdated(policyId, policies[policyId].version, policies[policyId].opsAllowed, policies[policyId].ctxSchema, newH);
        emit PolicyChanged(policyId, policies[policyId].version);
    }

    function deprecatePolicy(uint256 policyId) external onlyPolicyAdmin {
        if (policies[policyId].version == 0) revert PolicyNotFound();
        if (policies[policyId].isDeprecated) revert PolicyDeprecated();
        policies[policyId].isDeprecated = true;

        // free the hash so an identical policy can be recreated later if needed
        policyIdByHash[policies[policyId].policyHash] = 0;

        emit PolicyDeprecatedEvent(policyId);
        emit PolicyChanged(policyId, policies[policyId].version);
    }

    function getPolicy(uint256 policyId) external view returns (Policy memory) {
        return policies[policyId];
    }

    // =========================================================
    //                 GRANTS (RESOURCE-OWNER ONLY)
    // =========================================================
    function _grantKey(
        string memory fromNodeSignature,
        string memory toNodeSignature,
        uint256 policyId
    ) internal pure returns (bytes32) {
        // abi.encode with tuple to avoid collisions; includes policyId
        return keccak256(abi.encode(fromNodeSignature, toNodeSignature, policyId));
    }

    function issueGrant(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId,
        uint8 opsSubset,
        uint64 expiresAt
    ) external {
        _issueGrantCore(fromNodeSignature, toNodeSignature, policyId, opsSubset, expiresAt, false, 0);
    }

    function issueGrantDelegable(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId,
        uint8 opsSubset,
        uint64 expiresAt,
        bool delegationAllowed,
        uint8 delegationDepth
    ) external {
        _issueGrantCore(fromNodeSignature, toNodeSignature, policyId, opsSubset, expiresAt, delegationAllowed, delegationDepth);
    }

    function _issueGrantCore(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId,
        uint8 opsSubset,
        uint64 expiresAt,
        bool delegationAllowed,
        uint8 delegationDepth
    ) internal {
        string memory fromNodeId = nodeSignatureToNodeId[fromNodeSignature];
        string memory toNodeId   = nodeSignatureToNodeId[toNodeSignature];

        if (!iotNodes[fromNodeId].isRegistered || !iotNodes[toNodeId].isRegistered) revert NodeNotRegistered();
        // if (msg.sender != iotNodes[toNodeId].registeredBy) revert NotResourceOwner();

        Policy storage p = policies[policyId];
        if (p.version == 0) revert PolicyNotFound();
        if (p.isDeprecated) revert PolicyDeprecated();
        if (p.fromRole != iotNodes[fromNodeId].nodeType || p.toRole != iotNodes[toNodeId].nodeType) revert PolicyRoleMismatch();

        if (opsSubset == 0) revert EmptyOpsSubset();
        if ((opsSubset & ~p.opsAllowed) != 0) revert OpsSubsetExceedsAllowed();
        if (expiresAt <= uint64(block.timestamp)) revert InvalidExpiry();

        bytes32 grantId = _grantKey(fromNodeSignature, toNodeSignature, policyId);
        CapabilityGrant storage g = grants[grantId];

        // If there is an active grant for this exact tuple, allow a safe EXTEND/ADJUST instead of rejecting
        bool active = (g.isIssued && !g.isRevoked && uint64(block.timestamp) <= g.expiresAt);
        if (active) {
            // adjust: opsSubset must still be within policy; replacing allowed (do NOT enlarge beyond policy)
            g.opsSubset = opsSubset;
            // extend: only allow moving forward
            if (expiresAt > g.expiresAt) {
                g.expiresAt = expiresAt;
            }
            // keep existing delegation flags as is
            emit GrantExtended(grantId, g.expiresAt, g.opsSubset);
            return;
        }

        // fresh issue
        g.policyId          = uint32(policyId);
        g.opsSubset         = opsSubset;
        g.issuedAt          = uint64(block.timestamp);
        g.expiresAt         = expiresAt;
        g.isIssued          = true;
        g.isRevoked         = false;
        g.delegationAllowed = delegationAllowed;
        g.delegationDepth   = delegationDepth;
        g.parentGrantId     = bytes32(0);

        emit GrantIssued(grantId);
    }

    function delegateGrant(
        string calldata currentFromNodeSignature,  // holder of the parent grant
        string calldata toNodeSignature,
        string calldata newFromNodeSignature,      // child "from"
        uint256 policyId,
        uint8 opsSubset,
        uint64 expiresAt
    ) external {
        // Load parent grant by canonical key (currentFrom -> to, policyId)
        bytes32 parentId = _grantKey(currentFromNodeSignature, toNodeSignature, policyId);
        CapabilityGrant storage parent = grants[parentId];
        if (!parent.isIssued || parent.isRevoked) revert NotGrantHolder();
        if (uint64(block.timestamp) > parent.expiresAt) revert InvalidExpiry();
        if (!parent.delegationAllowed || parent.delegationDepth == 0) revert NotGrantHolder();

        // Holder-ownership check: caller must control the currentFrom node
        // string memory holderNodeId = nodeSignatureToNodeId[currentFromNodeSignature];
        // if (msg.sender != iotNodes[holderNodeId].registeredBy) revert NotGrantHolder();

        // Policy must be live, and the child must not exceed policy or parent
        Policy storage p = policies[parent.policyId];
        if (p.version == 0) revert PolicyNotFound();
        if (p.isDeprecated) revert PolicyDeprecated();
        if (opsSubset == 0) revert EmptyOpsSubset();
        if ((opsSubset & ~p.opsAllowed) != 0) revert OpsSubsetExceedsAllowed();
        if ((opsSubset & ~parent.opsSubset) != 0) revert OpsSubsetExceedsAllowed();
        if (expiresAt <= uint64(block.timestamp) || expiresAt > parent.expiresAt) revert InvalidExpiry();

        // Create/extend child grant (newFrom -> to, same policyId)
        bytes32 childId = _grantKey(newFromNodeSignature, toNodeSignature, policyId);
        CapabilityGrant storage c = grants[childId];

        // If active, allow safe adjust/extend under the same constraints
        if (c.isIssued && !c.isRevoked && uint64(block.timestamp) <= c.expiresAt) {
            c.opsSubset = opsSubset;
            if (expiresAt > c.expiresAt) {
                c.expiresAt = expiresAt;
            }
            // keep link/flags if already set
            emit GrantExtended(childId, c.expiresAt, c.opsSubset);
            return;
        }

        // Fresh child grant; propagate delegation flags and depth (minus one)
        c.policyId          = uint32(parent.policyId);
        c.opsSubset         = opsSubset;
        c.issuedAt          = uint64(block.timestamp);
        c.expiresAt         = expiresAt;
        c.isIssued          = true;
        c.isRevoked         = false;
        c.delegationAllowed = parent.delegationAllowed;
        unchecked { c.delegationDepth = parent.delegationDepth - 1; }
        c.parentGrantId     = parentId;

        emit GrantDelegated(parentId, childId, c.delegationDepth);
    }
    function revokeGrant(string calldata fromNodeSignature, string calldata toNodeSignature, uint256 policyId) external {
        bytes32 grantId = _grantKey(fromNodeSignature, toNodeSignature, policyId);
        // string memory toNodeId = nodeSignatureToNodeId[toNodeSignature];
        // if (msg.sender != iotNodes[toNodeId].registeredBy) revert NotResourceOwner();
        if (!grants[grantId].isIssued) revert PolicyNotFound();
        if (grants[grantId].isRevoked) revert AlreadyRevoked();

        grants[grantId].isRevoked = true;
        emit GrantRevoked(grantId);
    }

    function getGrant(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId
    )
        external
        view
        returns (uint256 policyIdOut, uint8 opsSubset, uint64 issuedAt, uint64 expiresAt, bool isIssued, bool isRevoked)
    {
        bytes32 id = _grantKey(fromNodeSignature, toNodeSignature, policyId);
        CapabilityGrant storage g = grants[id];
        return (g.policyId, g.opsSubset, g.issuedAt, g.expiresAt, g.isIssued, g.isRevoked);
    }

    function getGrantEx(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId
    )
        external
        view
        returns (
            uint256 policyIdOut,
            uint8 opsSubset,
            uint64 issuedAt,
            uint64 expiresAt,
            bool isIssued,
            bool isRevoked,
            bool delegationAllowed,
            uint8 delegationDepth
        )
    {
        bytes32 id = _grantKey(fromNodeSignature, toNodeSignature, policyId);
        CapabilityGrant storage g = grants[id];
        return (g.policyId, g.opsSubset, g.issuedAt, g.expiresAt, g.isIssued, g.isRevoked, g.delegationAllowed, g.delegationDepth);
    }

    function checkGrant(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId,
        uint8 opBit
    ) external view returns (bool) {
        bytes32 grantId = _grantKey(fromNodeSignature, toNodeSignature, policyId);
        CapabilityGrant storage g = grants[grantId];
        if (!g.isIssued || g.isRevoked) return false;
        if (uint64(block.timestamp) > g.expiresAt) return false;

        // parent must be valid if present
        if (g.parentGrantId != bytes32(0)) {
            CapabilityGrant storage parent = grants[g.parentGrantId];
            if (!parent.isIssued || parent.isRevoked) return false;
            if (uint64(block.timestamp) > parent.expiresAt) return false;
        }

        Policy storage p = policies[g.policyId];
        if (p.version == 0 || p.isDeprecated) return false;

        if ((g.opsSubset & opBit) == 0) return false;
        if ((p.opsAllowed & opBit) == 0) return false;

        return true;
    }

    function isGrantExpired(
        string calldata fromNodeSignature,
        string calldata toNodeSignature,
        uint256 policyId
    )
        external
        view
        returns (bool)
    {
        bytes32 grantId = _grantKey(fromNodeSignature, toNodeSignature, policyId);
        if (!grants[grantId].isIssued || grants[grantId].isRevoked) return true;
        return (uint64(block.timestamp) > grants[grantId].expiresAt);
    }

    // =========================================================
    //                         HELPERS
    // =========================================================
    function getNodeType(string memory nodeTypeStr) internal pure returns (NodeType) {
        bytes32 h = keccak256(abi.encodePacked(nodeTypeStr));
        if (h == keccak256(abi.encodePacked("Cloud")))    return NodeType.Cloud;
        if (h == keccak256(abi.encodePacked("Fog")))      return NodeType.Fog;
        if (h == keccak256(abi.encodePacked("Edge")))     return NodeType.Edge;
        if (h == keccak256(abi.encodePacked("Sensor")))   return NodeType.Sensor;
        if (h == keccak256(abi.encodePacked("Actuator"))) return NodeType.Actuator;
        return NodeType.Unknown;
    }

    function isValidator(string calldata nodeSignature) external view returns (bool) {
        string memory nodeId = nodeSignatureToNodeId[nodeSignature];

        if (
            !iotNodes[nodeId].isRegistered ||
            keccak256(abi.encodePacked(iotNodes[nodeId].nodeSignature)) !=
                keccak256(abi.encodePacked(nodeSignature))
        ) revert NodeNotRegistered();

        NodeType t = iotNodes[nodeId].nodeType;
        return (t == NodeType.Cloud || t == NodeType.Fog);
    }
}