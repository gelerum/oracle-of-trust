import json
import stat

from eth_account import Account

from scripts.bootstrap import bootstrap


def test_separate_secrets_and_idempotence(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    oracle = tmp_path / "oracle" / "oracle.key"
    factor = tmp_path / "factor" / "factor.key"
    buyer = tmp_path / "buyer" / "buyer.key"
    central_bank = tmp_path / "central-bank" / "central-bank.key"
    sources = tmp_path / "sources" / "source-keys.json"
    password = tmp_path / "database" / "password"
    for name, path in {"RUNTIME_DIR": runtime, "ORACLE_KEY_FILE": oracle,
                       "FACTOR_KEY_FILE": factor, "BUYER_KEY_FILE": buyer, "CENTRAL_BANK_KEY_FILE": central_bank, "SOURCE_KEYS_FILE": sources,
                       "DATABASE_PASSWORD_FILE": password}.items():
        monkeypatch.setenv(name, str(path))
    bootstrap()
    previous = oracle.read_text()
    source_keys = json.loads(sources.read_text())
    keys = [previous, factor.read_text(), buyer.read_text(), central_bank.read_text(), *source_keys.values()]
    assert len(set(keys)) == 6
    assert not (runtime / "oracle.key").exists()
    assert not (runtime / "source-keys.json").exists()
    assert stat.S_IMODE(oracle.stat().st_mode) == 0o600
    assert stat.S_IMODE(central_bank.stat().st_mode) == 0o600
    assert not (runtime / "central-bank.key").exists()
    assert json.loads((runtime / "oracle-signer.json").read_text())["address"] == Account.from_key(previous).address
    bootstrap()
    assert oracle.read_text() == previous
