import subprocess

# Path to your shell script
script_path = "/Users/khannmohsin/VSCode_Projects/MyDisIoT_Project/Node_fog/start_fog_services.sh"

# Argument to pass
arg = "start-blockchain"

# Run the shell script with the argument
result = subprocess.run(["bash", script_path, arg], capture_output=True, text=True)
print(result.stdout)  # Print the output of the script