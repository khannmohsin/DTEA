// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract NodeRegistry {
    
    enum NodeType { Unknown, Cloud, Fog, Edge, Sensor, Actuator } // Define possible node types

    struct IoTNode {
        string nodeName;
        NodeType nodeType;
        string publicKey;
        bool isRegistered;
        // string senderCapabilityToken;
        // string receiverCapabilityToken;
        address registeredBy;
        NodeType registeredByNodeType;
        string nodeSignature; 
    }

    struct CapabilityToken { 
        string policy;
        uint256 issuedAt;
        bool isIssued;
        bool isRevoked; 
    }

    mapping(string => IoTNode) public iotNodes;
    mapping(string => string) public nodeSignatureToNodeId;
    mapping(address => string) public addressToNodeId;
    mapping(bytes32 => CapabilityToken) public capabilityTokens; 

    // Event to signal validator proposal for off-chain listeners
    event ValidatorProposed(address indexed proposedBy, address indexed validator);   

    // Updated event: tokens are now a single string each.
    event NodeRegistered(
        string indexed nodeId,
        string nodeName,
        NodeType nodeType,
        string publicKey,
        // string senderCapabilityToken,
        // string receiverCapabilityToken,
        address registeredBy,
        NodeType registeredByNodeType,
        string nodeSignature
    );

    event TokenIssued( 
        string fromNodeSignature,
        string toNodeSignature,
        string policy,
        uint256 issuedAt
    );

    event TokenChecked( 
        string fromNodeSignature,
        string toNodeSignature
    );

    event TokenRevoked( 
        string fromNodeSignature,
        string toNodeSignature
    );

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

        // (string[] memory senderTokens, string[] memory receiverTokens) = generateToken(nodeType, regByNodeType);
        // string memory senderCapabilityToken = joinStringArray(senderTokens);
        // string memory receiverCapabilityToken = joinStringArray(receiverTokens);

        iotNodes[nodeId] = IoTNode({
            nodeName: nodeName,
            nodeType: nodeType,
            publicKey: publicKey,
            isRegistered: true,
            // senderCapabilityToken: senderCapabilityToken,
            // receiverCapabilityToken: receiverCapabilityToken,
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
            // senderCapabilityToken,
            // receiverCapabilityToken,
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

    function proposeValidator(address validator) public {
        require(validator != address(0), "Invalid validator address");
        emit ValidatorProposed(msg.sender, validator); 
    }

    function getNodeDetailsBySignature(string memory nodeSignature)
        public view returns (
            string memory,
            string memory,
            NodeType,
            string memory,
            bool,
            // string memory,
            // string memory,
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
            // node.senderCapabilityToken,
            // node.receiverCapabilityToken,
            node.registeredBy,
            node.nodeSignature,
            node.registeredByNodeType
        );
    }

    function getNodeDetailsByAddress(address nodeAddress)
        public view returns (
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
        require(bytes(nodeId).length > 0, "Address not registered");

        IoTNode memory node = iotNodes[nodeId];
        return (
            nodeId,
            node.nodeName,
            node.nodeType,
            node.publicKey,
            node.isRegistered,
            node.registeredBy,
            node.nodeSignature,
            node.registeredByNodeType
        );
    }

    function issueToken(string memory fromNodeSignature, string memory toNodeSignature) public {
        string memory fromNodeId = nodeSignatureToNodeId[fromNodeSignature];
        string memory toNodeId = nodeSignatureToNodeId[toNodeSignature];

        require(iotNodes[fromNodeId].isRegistered, "Sender not registered");
        require(iotNodes[toNodeId].isRegistered, "Receiver not registered");

        NodeType fromType = iotNodes[fromNodeId].nodeType;
        NodeType toType = iotNodes[toNodeId].nodeType;

        string memory policy = getPolicyString(fromType, toType);

        bytes32 tokenId = keccak256(abi.encodePacked(fromNodeSignature, toNodeSignature));

        // Modify this block to allow reissuing if previously revoked
        if (capabilityTokens[tokenId].isIssued && !capabilityTokens[tokenId].isRevoked) {
            revert("Token already issued and active");
        }

        capabilityTokens[tokenId] = CapabilityToken({
            policy: policy,
            issuedAt: block.timestamp,
            isIssued: true,
            isRevoked: false 
        });

        emit TokenIssued(fromNodeSignature, toNodeSignature, policy, block.timestamp);
    }

    function revokeToken(string memory fromNodeSignature, string memory toNodeSignature) public { 
        bytes32 tokenId = keccak256(abi.encodePacked(fromNodeSignature, toNodeSignature));
        require(capabilityTokens[tokenId].isIssued, "Token not issued");
        require(!capabilityTokens[tokenId].isRevoked, "Token already revoked");

        capabilityTokens[tokenId].isRevoked = true;
        emit TokenRevoked(fromNodeSignature, toNodeSignature);
    }

    function getToken(string memory fromNodeSignature, string memory toNodeSignature)
        public view returns (string memory policy, uint256 issuedAt, bool isIssued, bool isRevoked)
    {
        bytes32 tokenId = keccak256(abi.encodePacked(fromNodeSignature, toNodeSignature));
        CapabilityToken memory token = capabilityTokens[tokenId];
        return (token.policy, token.issuedAt, token.isIssued, token.isRevoked);
    }

    function checkToken(string memory fromNodeSignature, string memory toNodeSignature) public view returns (bool) {
        bytes32 tokenId = keccak256(abi.encodePacked(fromNodeSignature, toNodeSignature));
        CapabilityToken memory token = capabilityTokens[tokenId];
        return (token.isIssued && !token.isRevoked);
    }

    function isTokenExpired(string memory fromNodeSignature, string memory toNodeSignature, uint256 validityPeriodInSeconds)
        public view returns (bool) 
    {
        bytes32 tokenId = keccak256(abi.encodePacked(fromNodeSignature, toNodeSignature));
        CapabilityToken memory token = capabilityTokens[tokenId];
        if (!token.isIssued || token.isRevoked) {
            return true;
        }
        return (block.timestamp > token.issuedAt + validityPeriodInSeconds);
    }

    

    function getPolicyString(NodeType from, NodeType to) internal pure returns (string memory) { 
        if (from == NodeType.Cloud) {

            if (to == NodeType.Fog) {
                return "Cloud->Fog:READ,WRITE";
            } else if (to == NodeType.Edge) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Sensor) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Actuator) {
                return "NO POLICY DEFINED";
            }
        } else if (from == NodeType.Fog) {

            if (to == NodeType.Cloud) {
                return "Fog->Cloud:READ,WRITE";
            } else if (to == NodeType.Edge) {
                return "Fog->Edge:READ,WRITE";
            } else if (to == NodeType.Sensor) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Actuator) {
                return "NO POLICY DEFINED";
            }   
        } else if (from == NodeType.Edge) {

            if (to == NodeType.Cloud) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Fog) {
                return "Edge->Fog:READ,WRITE";
            } else if (to == NodeType.Sensor) {
                return "Edge->Sensor:EXECUTE";
            } else if (to == NodeType.Actuator) {
                return "Edge->Actuator:EXECUTE";
            }
        } else if (from == NodeType.Sensor) {

            if (to == NodeType.Cloud) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Fog) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Edge) {
                return "Sensor->Edge:READ";
            } else if (to == NodeType.Actuator) {
                return "NO POLICY DEFINED";
            }
        } else if (from == NodeType.Actuator) {

            if (to == NodeType.Cloud) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Fog) {
                return "NO POLICY DEFINED";
            } else if (to == NodeType.Edge) {
                return "Actuator->Edge:READ,WRITE,EXECUTE";
            } else if (to == NodeType.Sensor) {
                return "NO POLICY DEFINED";
            }
        }
        return "INVALID_POLICY: NO POLICY ASSIGNED FOR THIS COMBINATION";
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

    function isValidator(string memory nodeSignature) public view returns (bool) {
        string memory nodeId = nodeSignatureToNodeId[nodeSignature];
        require(
            iotNodes[nodeId].isRegistered &&
            keccak256(abi.encodePacked(iotNodes[nodeId].nodeSignature)) == keccak256(abi.encodePacked(nodeSignature)),
            "Node not found or invalid signature"
        );

        NodeType nodeType = iotNodes[nodeId].nodeType;

        if (nodeType == NodeType.Cloud || nodeType == NodeType.Fog) {
            return true;
        }

        return false;
    }

}