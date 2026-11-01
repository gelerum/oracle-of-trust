import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import psycopg
from psycopg import sql
import pytest
from eth_account import Account
from web3 import Web3

from oracle.crypto import sign_source
from oracle.models import FactoringDeal as Deal
from oracle.registry import Registry, load_json
from oracle.examples import demo_factories
from oracle.rules import RulesEngine
from oracle.service import Gateway
from oracle.store import Store


@pytest.fixture
def deal():
    return Deal(dealType="FACTORING", dealId="0x" + "11" * 32, documentHash="0x" + "22" * 32,
                subjectRef="test-supply-001", claimId="claim-fixture-001", supplier=Account.create().address,
                buyer=Account.create().address, factor=Account.create().address,
                claimAmount=10_000_000_000_000, financingAmount=9_000_000_000_000,
                dueDate=int(time.time()) + 60 * 86400)


@pytest.fixture
def keys():
    return {name: Account.create().key.hex() for name in ("mock-edo", "mock-vtb", "oracle")}


@pytest.fixture
def rules():
    return RulesEngine(load_json("config/rules.json"))


@pytest.fixture
def registry(keys):
    return Registry(load_json("config/sources.json"),
                    {s: Account.from_key(k).address for s, k in keys.items() if s != "oracle"}, factories=demo_factories())


@pytest.fixture
def evidence_factory(deal, keys, rules):
    def make(fact="DELIVERY_ACCEPTED", **changes):
        source = rules.rules["FACTORING"][fact]["sources"][0]
        rule = rules.rules["FACTORING"][fact]
        now = int(time.time())
        data = {"schemaVersion": "1", "evidenceId": str(uuid.uuid4()), "sourceId": source,
                "factType": fact, "dealId": deal.dealId, "subjectRef": deal.subjectRef,
                "value": True, "retrievalStatus": "present", "documentHash": deal.documentHash,
                "observedAt": now, "expiresAt": now + 300,
                "attributes": {**{k: getattr(deal, v) for k, v in rule["attributes"].items()}, **rule["required"]}}
        data.update(changes)
        data["sourceProof"] = sign_source(data, keys[source])
        return data
    return make


@pytest.fixture
def store():
    dsn = os.environ["DATABASE_URL"]
    schema = "test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    test_dsn = dsn + ("&" if "?" in dsn else "?") + "options=" + quote("-csearch_path=" + schema)
    instance = Store(test_dsn)
    instance.initialize()
    yield instance
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def gateway(store, registry, rules, keys, deal, evidence_factory):
    oracle = Account.from_key(keys["oracle"]).address
    chain = SimpleNamespace(chain_id=31337, address=Account.create().address, oracle_address=oracle,
                            validate_deal=lambda d: None,
                            contract=SimpleNamespace(functions=SimpleNamespace(
                                oracle=lambda: SimpleNamespace(call=lambda: oracle))))
    class Adapter:
        async def fetch_fact(self, query, fact):
            return evidence_factory(fact)
    registry.adapters = {source: Adapter() for source in registry.sources}
    store.put_deal(deal.model_dump())
    return Gateway(registry, rules, store, chain, keys["oracle"])
