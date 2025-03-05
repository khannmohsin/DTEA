const FogNodeRegistry = artifacts.require("FogNodeRegistry");

module.exports = function (deployer, network, accounts) {
    deployer.deploy(FogNodeRegistry);
};