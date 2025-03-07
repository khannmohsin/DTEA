// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract NodeRegistry {
    
    enum NodeType { Unknown, Fog, Edge, Sensor, Actuator } // Define possible node types

    struct IoTNode {
        string nodeName;
        NodeType nodeType;
        string publicKey;
        bool isRegistered;
        string capabilityToken;
    }

    mapping(string => IoTNode) public iotNodes;
    
    event NodeRegistered(string indexed nodeId, string nodeName, NodeType nodeType, string publicKey, string capabilityToken);

    function registerNode(string memory nodeId, string memory nodeName, string memory nodeTypeStr, string memory publicKey) public {
        require(!iotNodes[nodeId].isRegistered, "Node already registered!");

        NodeType nodeType = getNodeType(nodeTypeStr);
        require(nodeType != NodeType.Unknown, "Invalid node type!");

        string memory capabilityToken = generateToken(nodeType);

        iotNodes[nodeId] = IoTNode({
            nodeName: nodeName,
            nodeType: nodeType,
            publicKey: publicKey,
            isRegistered: true,
            capabilityToken: capabilityToken
        });

        emit NodeRegistered(nodeId, nodeName, nodeType, publicKey, capabilityToken);
    }

    function isNodeRegistered(string memory nodeId) public view returns (bool) {
        return iotNodes[nodeId].isRegistered;
    }

    function getNodeDetails(string memory nodeId) public view returns (string memory, NodeType, string memory, bool, string memory) {
        require(iotNodes[nodeId].isRegistered, "Node not found!");
        IoTNode memory node = iotNodes[nodeId];
        return (node.nodeName, node.nodeType, node.publicKey, node.isRegistered, node.capabilityToken);
    }

    function getNodeType(string memory nodeTypeStr) internal pure returns (NodeType) {
        bytes32 nodeTypeHash = keccak256(abi.encodePacked(nodeTypeStr));

        if (nodeTypeHash == keccak256(abi.encodePacked("Fog"))) return NodeType.Fog;
        if (nodeTypeHash == keccak256(abi.encodePacked("Edge"))) return NodeType.Edge;
        if (nodeTypeHash == keccak256(abi.encodePacked("Sensor"))) return NodeType.Sensor;
        if (nodeTypeHash == keccak256(abi.encodePacked("Actuator"))) return NodeType.Actuator;

        return NodeType.Unknown; // Invalid type
    }

    function generateToken(NodeType nodeType) internal pure returns (string memory) {
        if (nodeType == NodeType.Fog) return "TOKEN_FOG: Can process and store data.";
        if (nodeType == NodeType.Edge) return "TOKEN_EDGE: Can process data locally.";
        if (nodeType == NodeType.Sensor) return "TOKEN_SENSOR: Can collect and send data.";
        if (nodeType == NodeType.Actuator) return "TOKEN_ACTUATOR: Can execute actions.";
        
        return "INVALID_TOKEN";
    }
}