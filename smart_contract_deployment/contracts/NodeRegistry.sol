// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract NodeRegistry {
    
    enum NodeType { Unknown, Cloud, Fog, Edge, Sensor, Actuator } // Define possible node types

    struct IoTNode {
        string nodeName;
        NodeType nodeType;
        string publicKey;
        bool isRegistered;
        string senderCapabilityToken;
        string receiverCapabilityToken;
        address registeredBy;
        NodeType registeredByNodeType;
        string nodeSignature; 
    }

    mapping(string => IoTNode) public iotNodes;
    mapping(string => string) public nodeSignatureToNodeId;
    mapping(address => string) public addressToNodeId;
    
    // Updated event: tokens are now a single string each.
    event NodeRegistered(
        string indexed nodeId,
        string nodeName,
        NodeType nodeType,
        string publicKey,
        string senderCapabilityToken,
        string receiverCapabilityToken,
        address registeredBy,
        NodeType registeredByNodeType,
        string nodeSignature
    );
    
    // Helper function to join an array of strings with commas.
    function joinStringArray(string[] memory arr) internal pure returns (string memory) {
        bytes memory result;
        for (uint i = 0; i < arr.length; i++) {
            result = abi.encodePacked(result, arr[i]);
            if (i < arr.length - 1) {
                result = abi.encodePacked(result, ",");
            }
        }
        return string(result);
    }

    function registerNode(
        string memory nodeId,
        string memory nodeName,
        string memory nodeTypeStr,
        string memory publicKey,
        address registeredBy,
        string memory registeredByNodeTypeStr,
        string memory nodeSignature 
    ) public {
        require(bytes(nodeSignatureToNodeId[nodeSignature]).length == 0, "Node already registered!");
        nodeSignatureToNodeId[nodeSignature] = nodeId;
        NodeType nodeType = getNodeType(nodeTypeStr);
        NodeType regByNodeType = getNodeType(registeredByNodeTypeStr);
        require(nodeType != NodeType.Unknown, "Invalid node type!");
        require(regByNodeType != NodeType.Unknown, "Invalid registered-by node type!");

        (string[] memory senderTokens, string[] memory receiverTokens) = generateToken(nodeType, regByNodeType);
        string memory senderCapabilityToken = joinStringArray(senderTokens);
        string memory receiverCapabilityToken = joinStringArray(receiverTokens);

        iotNodes[nodeId] = IoTNode({
            nodeName: nodeName,
            nodeType: nodeType,
            publicKey: publicKey,
            isRegistered: true,
            senderCapabilityToken: senderCapabilityToken,
            receiverCapabilityToken: receiverCapabilityToken,
            registeredBy: registeredBy,
            registeredByNodeType: regByNodeType,
            nodeSignature: nodeSignature
        });

        addressToNodeId[registeredBy] = nodeId;

        emit NodeRegistered(
            nodeId,
            nodeName,
            nodeType,
            publicKey,
            senderCapabilityToken,
            receiverCapabilityToken,
            registeredBy,
            regByNodeType,
            nodeSignature
        );
    }

    function isNodeRegistered(string memory nodeSignature) public view returns (bool) {
        string memory nodeId = nodeSignatureToNodeId[nodeSignature];
        return (
            iotNodes[nodeId].isRegistered &&
            keccak256(abi.encodePacked(iotNodes[nodeId].nodeSignature)) == keccak256(abi.encodePacked(nodeSignature))
        );
    }

    function getNodeDetailsBySignature(string memory nodeSignature)
        public view returns (
            string memory,
            string memory,
            NodeType,
            string memory,
            bool,
            string memory,
            string memory,
            address,
            string memory,
            NodeType
        )
    {
        string memory nodeId = nodeSignatureToNodeId[nodeSignature];
        require(
            iotNodes[nodeId].isRegistered &&
            keccak256(abi.encodePacked(iotNodes[nodeId].nodeSignature)) == keccak256(abi.encodePacked(nodeSignature)),
            "No matching node registered with this signature"
        );

        IoTNode memory node = iotNodes[nodeId];
        return (
            nodeId,
            node.nodeName,
            node.nodeType,
            node.publicKey,
            node.isRegistered,
            node.senderCapabilityToken,
            node.receiverCapabilityToken,
            node.registeredBy,
            node.nodeSignature,
            node.registeredByNodeType
        );
    }

    function getNodeType(string memory nodeTypeStr) internal pure returns (NodeType) {
        bytes32 nodeTypeHash = keccak256(abi.encodePacked(nodeTypeStr));

        if (nodeTypeHash == keccak256(abi.encodePacked("Cloud"))) return NodeType.Cloud;
        if (nodeTypeHash == keccak256(abi.encodePacked("Fog"))) return NodeType.Fog;
        if (nodeTypeHash == keccak256(abi.encodePacked("Edge"))) return NodeType.Edge;
        if (nodeTypeHash == keccak256(abi.encodePacked("Sensor"))) return NodeType.Sensor;
        if (nodeTypeHash == keccak256(abi.encodePacked("Actuator"))) return NodeType.Actuator;

        return NodeType.Unknown; // Invalid type
    }

    // Updated generateToken: returns arrays of strings.
    function generateToken(NodeType nodeType, NodeType registeredByNodeType) 
        internal pure returns (string[] memory, string[] memory) 
    {
        string[] memory nodeTypePermissions;
        string[] memory registeredByNodeTypePermissions;
        
        if (nodeType == NodeType.Fog && registeredByNodeType == NodeType.Cloud) {
            nodeTypePermissions = new string[](3);
            nodeTypePermissions[0] = "Fog:READ";
            nodeTypePermissions[1] = "Fog:WRITE";
            nodeTypePermissions[2] = "Fog:TRANSMIT";
            
            registeredByNodeTypePermissions = new string[](2);
            registeredByNodeTypePermissions[0] = "Cloud:READ";
            registeredByNodeTypePermissions[1] = "Cloud:EXECUTE";
        } else if (nodeType == NodeType.Edge && registeredByNodeType == NodeType.Fog) {
            nodeTypePermissions = new string[](3);
            nodeTypePermissions[0] = "Edge:READ";
            nodeTypePermissions[1] = "Edge:WRITE";
            nodeTypePermissions[2] = "Edge:TRANSMIT";
            
            registeredByNodeTypePermissions = new string[](2);
            registeredByNodeTypePermissions[0] = "Fog:READ";
            registeredByNodeTypePermissions[1] = "Fog:EXECUTE";
        } else if (nodeType == NodeType.Sensor && registeredByNodeType == NodeType.Edge) {
            nodeTypePermissions = new string[](2);
            nodeTypePermissions[0] = "Sensor:READ";
            nodeTypePermissions[1] = "Sensor:TRANSMIT";
            
            registeredByNodeTypePermissions = new string[](2);
            registeredByNodeTypePermissions[0] = "Edge:READ";
            registeredByNodeTypePermissions[1] = "Edge:EXECUTE";
        } else if (nodeType == NodeType.Actuator && registeredByNodeType == NodeType.Edge) {
            nodeTypePermissions = new string[](3);
            nodeTypePermissions[0] = "Actuator:READ";
            nodeTypePermissions[1] = "Actuator:WRITE";
            nodeTypePermissions[2] = "Actuator:EXECUTE";
            
            registeredByNodeTypePermissions = new string[](2);
            registeredByNodeTypePermissions[0] = "Edge:READ";
            registeredByNodeTypePermissions[1] = "Edge:EXECUTE";
        } else {
            nodeTypePermissions = new string[](1);
            nodeTypePermissions[0] = "INVALID_TOKEN";
            registeredByNodeTypePermissions = new string[](1);
            registeredByNodeTypePermissions[0] = "INVALID_TOKEN";
        }

        return (nodeTypePermissions, registeredByNodeTypePermissions);
    }
}