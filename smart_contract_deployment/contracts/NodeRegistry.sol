// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract NodeRegistry {
    
    enum NodeType { Unknown, Cloud, Fog, Edge, Sensor, Actuator } // Define possible node types

    struct IoTNode {
        string nodeName;
        NodeType nodeType;
        string publicKey;
        bool isRegistered;
        // We store the tokens as a single string (joined with commas)
        string senderCapabilityToken;
        string receiverCapabilityToken;
        address registeredBy;
        NodeType registeredByNodeType;
    }

    mapping(string => IoTNode) public iotNodes;
    
    // Updated event: tokens are now a single string each.
    event NodeRegistered(
        string indexed nodeId,
        string nodeName,
        NodeType nodeType,
        string publicKey,
        string senderCapabilityToken,
        string receiverCapabilityToken,
        address registeredBy,
        NodeType registeredByNodeType 
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
        string memory registeredByNodeTypeStr
    ) public {
        require(!iotNodes[nodeId].isRegistered, "Node already registered!");

        NodeType nodeType = getNodeType(nodeTypeStr);
        NodeType regByNodeType = getNodeType(registeredByNodeTypeStr);
        require(nodeType != NodeType.Unknown, "Invalid node type!");
        require(regByNodeType != NodeType.Unknown, "Invalid registered by node type!");

        // Get arrays of permissions
        (string[] memory senderTokens, string[] memory receiverTokens) = generateToken(nodeType, regByNodeType);  
        // Join the arrays into single strings
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
            registeredByNodeType: regByNodeType
        });

        emit NodeRegistered(nodeId, nodeName, nodeType, publicKey, senderCapabilityToken, receiverCapabilityToken, registeredBy, regByNodeType);
    }

    function isNodeRegistered(string memory nodeId) public view returns (bool) {
        return iotNodes[nodeId].isRegistered;
    }

    function getNodeDetails(string memory nodeId) 
        public view returns (
            string memory, 
            NodeType, 
            string memory, 
            bool, 
            string memory, 
            string memory, 
            address
        ) 
    {
        require(iotNodes[nodeId].isRegistered, "Node not found!");
        IoTNode memory node = iotNodes[nodeId];
        return (
            node.nodeName, 
            node.nodeType, 
            node.publicKey, 
            node.isRegistered, 
            node.senderCapabilityToken, 
            node.receiverCapabilityToken, 
            node.registeredBy
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