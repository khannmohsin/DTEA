import subprocess
import time
import os

# Define the commands to run in separate terminals
# ✅ Use raw string `r""` or double quotes around the path
blockchain_command = 'python3 "/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/blockchain_init.py"'
flask_command = 'python3 "/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/node_registration.py"'

def run_blockchain():
    """Opens a new terminal window and runs the blockchain node."""
    subprocess.run([
        "osascript", "-e",
        f'tell application "Terminal" to do script "{blockchain_command}"'
    ])

def run_flask():
    """Opens a new terminal window and runs the Flask API."""
    subprocess.run([
        "osascript", "-e",
        f'tell application "Terminal" to do script "{flask_command}"'
    ])

if __name__ == "__main__":
    print("Starting Blockchain and Flask API in separate terminal windows...")
    
    run_blockchain()
    time.sleep(2)  # Wait for Blockchain to start before Flask
    run_flask()

    print("✅ Both processes started successfully!")

# import multiprocessing
# import sys
# import os
# from blockchain_init import BlockchainInit
# from node_registration import NodeRegistry
# import time

# def run_blockchain():
#     """Start the blockchain node using Besu and log output in real-time."""
#     blockchain = BlockchainInit()
#     blockchain.create_qbft_file(num_prefunded_accounts=3, num_validators=1)
#     blockchain.generate_keys()
#     blockchain.create_genesis_file(qbft_config_path="/Users/khannmohsin/VSCode Projects/MyDisIoT Project/Node_cloud/qbftConfigFile.json")
#     blockchain.update_genesis_file()
#     time.sleep(2)
#     blockchain.update_extra_data_in_genesis()

#     # Open log file
#     with open("blockchain.log", "a", buffering=1) as log_file:
#         process = blockchain.start_blockchain_node()

#         # Stream output in real-time
#         for line in process.stdout:
#             print(f"[Blockchain] {line.strip()}", flush=True)  # Print to terminal
#             log_file.write(f"[Blockchain] {line}")  # Write to log file

#         for line in process.stderr:
#             print(f"[Blockchain] {line.strip()}", flush=True, file=sys.stderr)
#             log_file.write(f"[Blockchain] {line}")

# def run_node_registry():
#     """Start the Flask API for node registration and log output in real-time."""
#     node_registry = NodeRegistry()

#     # Open log file
#     with open("flask.log", "a", buffering=1) as log_file:
#         sys.stdout = log_file  # Redirect stdout to file
#         sys.stderr = log_file  # Redirect stderr to file

#         print("[Flask] Starting Node Registry API...", flush=True)
#         node_registry.run()

# if __name__ == "__main__":

#     blockchain_process = multiprocessing.Process(target=run_blockchain)
#     flask_process = multiprocessing.Process(target=run_node_registry)

#     blockchain_process.start()
#     flask_process.start()

#     blockchain_process.join()
#     flask_process.join()