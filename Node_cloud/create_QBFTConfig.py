import json
from eth_account import Account

# Constants
CONFIG_FILE = "qbftConfigFile.json"
KEYS_FILE = "generated_keys.json"
NUM_VALIDATORS = 1  # Number of validators in the QBFT network
NUM_PREFUNDED_ACCOUNTS = 3  # Number of prefunded accounts
CHAIN_ID = 1337
BLOCK_PERIOD_SECONDS = 2
EPOCH_LENGTH = 30000
REQUEST_TIMEOUT_SECONDS = 4
GAS_LIMIT = "0x47b760"
DIFFICULTY = "0x1"

# Function to generate a new Ethereum account
def generate_account():
    """Generates a new Ethereum account (private key and address)."""
    account = Account.create()
    return {
        "private_key": account._private_key.hex(),  # Store as hex
        "address": account.address
    }

# Generate validator nodes
def generate_validators(num_validators):
    return [generate_account() for _ in range(num_validators)]

# Generate prefunded accounts
def generate_prefunded_accounts(num_accounts):
    return [generate_account() for _ in range(num_accounts)]

# Create the QBFT configuration file
def create_qbft_config():
    # Generate validators and prefunded accounts
    validator_nodes = generate_validators(NUM_VALIDATORS)
    prefunded_accounts = generate_prefunded_accounts(NUM_PREFUNDED_ACCOUNTS)

    # Extract validator addresses
    validator_addresses = [validator["address"] for validator in validator_nodes]

    # Create the QBFT configuration
    qbft_config = {
        "genesis": {
            "config": {
                "chainId": CHAIN_ID,
                "berlinBlock": 0,
                "qbft": {
                    "blockperiodseconds": BLOCK_PERIOD_SECONDS,
                    "epochlength": EPOCH_LENGTH,
                    "requesttimeoutseconds": REQUEST_TIMEOUT_SECONDS
                }
            },
            "nonce": "0x0",
            "timestamp": "0x58ee40ba",
            "gasLimit": GAS_LIMIT,
            "difficulty": DIFFICULTY,
            # "mixHash": "0x63746963616c2062797a616e74696e6365206674756c7420746f6c6572616e6365",
            "coinbase": "0x0000000000000000000000000000000000000000",
            "alloc": {acct["address"]: {"balance": "90000000000000000000000"} for acct in prefunded_accounts}
        },
        "blockchain": {
            "nodes": {
                "generate": True,  # We generated validators manually
                "count": NUM_VALIDATORS
            }
        }
    }

    # Save the configuration file
    with open(CONFIG_FILE, "w") as f:
        json.dump(qbft_config, f, indent=4)

    # Save generated keys for later use
    with open(KEYS_FILE, "w") as f:
        json.dump({
            "validators": validator_nodes,
            "prefunded_accounts": prefunded_accounts
        }, f, indent=4)

    print(f"✅ QBFT Configuration file `{CONFIG_FILE}` created successfully!")
    print(f"✅ Validator and prefunded account keys saved in `{KEYS_FILE}`")

# Run the script
if __name__ == "__main__":
    create_qbft_config()