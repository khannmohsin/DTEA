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

    print(" Both processes started successfully!")
