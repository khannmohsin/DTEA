const NodeRegistry = artifacts.require("NodeRegistry");

module.exports = function (deployer) {
  deployer.deploy(NodeRegistry);
};