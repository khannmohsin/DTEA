import multiprocessing
import sys
import os
from blockchain_init import BlockchainInit
from node_registration import NodeRegistry
import time

def run_blockchain():
    """Start the blockchain node using Besu and log output in real-time."""
    blockchain = BlockchainInit()
    blockchain.create_qbft_file(num_prefunded_accounts=3, num_validators=1)
    blockchain.generate_keys()
    blockchain.create_genesis_file(qbft_config_path="/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/qbftConfigFile.json")

    # Open log file
    with open("blockchain.log", "a", buffering=1) as log_file:
        process = blockchain.start_blockchain_node()

        # Stream output in real-time
        for line in process.stdout:
            print(f"[Blockchain] {line.strip()}", flush=True)  # Print to terminal
            log_file.write(f"[Blockchain] {line}")  # Write to log file

        for line in process.stderr:
            print(f"[Blockchain] {line.strip()}", flush=True, file=sys.stderr)
            log_file.write(f"[Blockchain] {line}")

def run_node_registry():
    """Start the Flask API for node registration and log output in real-time."""
    node_registry = NodeRegistry()

    # Open log file
    with open("flask.log", "a", buffering=1) as log_file:
        sys.stdout = log_file  # Redirect stdout to file
        sys.stderr = log_file  # Redirect stderr to file

        print("[Flask] Starting Node Registry API...", flush=True)
        node_registry.run()

if __name__ == "__main__":

    blockchain = BlockchainInit()
    blockchain.create_qbft_file(num_prefunded_accounts=3, num_validators=1)
    blockchain.generate_keys()
    blockchain.create_genesis_file(qbft_config_path="/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/qbftConfigFile.json")
    blockchain.update_genesis_file()
    time.sleep(2)
    blockchain.update_extra_data_in_genesis()
    blockchain.start_blockchain_node()
    # Start Blockchain (Besu) and Flask (Node Registry) in parallel
    # blockchain_process = multiprocessing.Process(target=run_blockchain)
    # flask_process = multiprocessing.Process(target=run_node_registry)

    # blockchain_process.start()
    # flask_process.start()

    # blockchain_process.join()
    # flask_process.join()