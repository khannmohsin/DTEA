import json
from pathlib import Path

from root_blockchain_init import BlockchainInit


def test_create_qbft_file_uses_fast_consensus_timing(tmp_path, monkeypatch):
    init = BlockchainInit()
    init.config_file = str(tmp_path / "qbftConfigFile.json")
    init.prefunded_account_file = str(tmp_path / "prefunded_keys.json")
    counter = {"value": 0}

    def fake_account():
        counter["value"] += 1
        return {
            "private_key": f"key-{counter['value']}",
            "address": f"0x{counter['value']:040x}",
        }

    monkeypatch.setattr(init, "generate_account", fake_account)

    init.create_qbft_file(num_prefunded_accounts=2, num_validators=1)

    payload = json.loads(Path(init.config_file).read_text())
    qbft = payload["genesis"]["config"]["qbft"]

    assert qbft["blockperiodseconds"] == 1
    assert qbft["requesttimeoutseconds"] == 2
