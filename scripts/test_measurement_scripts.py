import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"


class FileBackup:
    def __init__(self, *paths: Path):
        self.paths = paths
        self.backup_dir = RESULTS_DIR / ".test-backups"

    def __enter__(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        for path in self.paths:
            backup = self.backup_dir / path.name
            if path.exists():
                shutil.copy2(path, backup)
            elif backup.exists():
                backup.unlink()
        return self

    def __exit__(self, exc_type, exc, tb):
        for path in self.paths:
            backup = self.backup_dir / path.name
            if backup.exists():
                shutil.copy2(backup, path)
                backup.unlink()
            elif path.exists():
                path.unlink()
        try:
            self.backup_dir.rmdir()
        except OSError:
            pass


def test_measure_gas_script_aggregates_jsonl():
    gas_log = RESULTS_DIR / "gas_log.jsonl"
    gas_summary = RESULTS_DIR / "gas_summary.json"

    with FileBackup(gas_log, gas_summary):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        gas_log.write_text(
            "\n".join([
                json.dumps({"function": "issueToken", "gasUsed": 100}),
                json.dumps({"function": "issueToken", "gasUsed": 200}),
                json.dumps({"function": "revokeToken", "gasUsed": 50}),
            ]) + "\n"
        )

        subprocess.run(
            ["node", "scripts/measure_gas.js"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(gas_summary.read_text())
        assert payload["issueToken"]["count"] == 2
        assert payload["issueToken"]["mean_gas_used"] == 150
        assert payload["revokeToken"]["count"] == 1


def test_collect_contract_metrics_script_writes_expected_contract_rows():
    output_path = RESULTS_DIR / "contract_metrics.json"

    with FileBackup(output_path):
        subprocess.run(
            ["node", "scripts/collect_contract_metrics.js"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        rows = json.loads(output_path.read_text())
        names = {row["contract"] for row in rows}
        assert {"NodeRegistry", "CapabilityGrant", "ValidatorGovernance", "PolicyMultisig", "TOTALS"} <= names
