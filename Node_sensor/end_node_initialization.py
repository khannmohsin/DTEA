import os
import sys
from eth_keys import keys

class KeyGenerator:
    def __init__(self):
        self.root_path = os.path.dirname(os.path.abspath(__file__))
        self.data_path = os.path.join(self.root_path, "data/")
        self.private_key_path = os.path.join(self.data_path, "key.priv")
        self.public_key_path = os.path.join(self.data_path, "key.pub")

    def generate_keys(self):
        """Generates a new node private and public key pair and saves them to files."""
        os.makedirs(self.data_path, exist_ok=True)

        private_key_bytes = os.urandom(32)
        priv_key = keys.PrivateKey(private_key_bytes)

        with open(self.private_key_path, "w") as priv_file:
            priv_file.write(priv_key.to_hex())

        with open(self.public_key_path, "w") as pub_file:
            pub_file.write(priv_key.public_key.to_hex())

        print("Keys generated:")
        print(f"Private Key: {self.private_key_path}")
        print(f"Public Key : {self.public_key_path}")

if __name__ == "__main__":
    generator = KeyGenerator()

    if len(sys.argv) > 1 and sys.argv[1] == "generate_keys":
        generator.generate_keys()
    else:
        print("Usage: python keygen_dispatchable.py generate_keys")