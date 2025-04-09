from eth_keys import keys
from eth_account import Account
import os

def generate_keys(data_path, private_key_path, public_key_path):
    """Generates a new Ethereum account (private key and address)."""
    os.makedirs(data_path, exist_ok=True)

    with open(private_key_path, "w") as priv_file:
        private_key_bytes = os.urandom(32)
        priv_key = keys.PrivateKey(private_key_bytes)
        priv_file.write(priv_key.to_hex())

    with open(public_key_path, "w") as pub_file:
        public_key = priv_key.public_key
        pub_file.write(public_key.to_hex())

# Example usage
data_path = "./data"
private_key_path = "./data/private_key.priv"
public_key_path = "./data/public_key.pub"
generate_keys(data_path, private_key_path, public_key_path)