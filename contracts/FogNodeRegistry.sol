// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract FogNodeRegistry {
    
    struct FogNode {
        string nodeName;
        string nodeType;
        string publicKey;
        bool isRegistered;
    }

    mapping(string => FogNode) public fogNodes;
    
    event FogNodeRegistered(string indexed fogId, string nodeName, string nodeType, string publicKey);

    function registerFogNode(string memory fogId, string memory nodeName, string memory nodeType, string memory publicKey) public {
        require(!fogNodes[fogId].isRegistered, "Fog Node already registered!");

        fogNodes[fogId] = FogNode({
            nodeName: nodeName,
            nodeType: nodeType,
            publicKey: publicKey,
            isRegistered: true
        });

        emit FogNodeRegistered(fogId, nodeName, nodeType, publicKey);
    }

    function isFogNodeRegistered(string memory fogId) public view returns (bool) {
        return fogNodes[fogId].isRegistered;
    }

    function getFogNode(string memory fogId) public view returns (string memory, string memory, string memory, bool) {
        require(fogNodes[fogId].isRegistered, "Fog Node not found!");
        FogNode memory node = fogNodes[fogId];
        return (node.nodeName, node.nodeType, node.publicKey, node.isRegistered);
    }
}