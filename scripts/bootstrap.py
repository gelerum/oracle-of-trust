import json
import os
import secrets
from pathlib import Path

from eth_account import Account


def bootstrap():
    """Generate separate demo credentials in a private runtime volume."""
    root = Path(os.getenv("RUNTIME_DIR", "/runtime"))
    root.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    paths = {
        "central_bank": Path(os.getenv("CENTRAL_BANK_KEY_FILE", str(root / "central-bank.key"))),
        "oracle": Path(os.getenv("ORACLE_KEY_FILE", str(root / "oracle.key"))),
        "buyer": Path(os.getenv("BUYER_KEY_FILE", str(root / "buyer.key"))),
        "factor": Path(os.getenv("FACTOR_KEY_FILE", str(root / "factor.key"))),
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("0x" + secrets.token_hex(32))
    (root / "oracle-signer.json").write_text(json.dumps({
        "address": Account.from_key(paths["oracle"].read_text().strip()).address}))
    source_path = Path(os.getenv("SOURCE_KEYS_FILE", str(root / "source-keys.json")))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    if not source_path.exists():
        source_path.write_text(json.dumps({source: "0x" + secrets.token_hex(32)
                                          for source in ("mock-edo", "mock-vtb")}))
    keys = json.loads(source_path.read_text())
    (root / "source-signers.json").write_text(json.dumps({source: Account.from_key(key).address
                                                        for source, key in keys.items()}))
    password = Path(os.getenv("DATABASE_PASSWORD_FILE", str(root / "postgres-password")))
    password.parent.mkdir(parents=True, exist_ok=True)
    if not password.exists():
        password.write_text(secrets.token_hex(24))
    password.chmod(0o444)


if __name__ == "__main__":
    bootstrap()
