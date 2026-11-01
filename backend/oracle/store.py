import json
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


class Store:
    """Persist audit, evidence identities and issued attestations in PostgreSQL.

    Args:
        dsn: PostgreSQL connection string.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn

    def initialize(self):
        """Apply the idempotent initial schema."""
        with psycopg.connect(self.dsn) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS deals (
                    id TEXT PRIMARY KEY, data JSONB NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id BIGSERIAL PRIMARY KEY, deal_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, fact_type TEXT NOT NULL,
                    raw JSONB NOT NULL, accepted BOOLEAN NOT NULL,
                    reason TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
                CREATE TABLE IF NOT EXISTS evidence_seen (
                    source_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                    PRIMARY KEY(source_id, evidence_id));
                CREATE TABLE IF NOT EXISTS evaluations (
                    id BIGSERIAL PRIMARY KEY, deal_id TEXT NOT NULL, result JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
                CREATE TABLE IF NOT EXISTS attestations (
                    nonce BIGSERIAL PRIMARY KEY, deal_id TEXT NOT NULL,
                    payload JSONB, signature TEXT, evidence_ids JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
                ALTER TABLE audit ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;
                CREATE INDEX IF NOT EXISTS audit_source_cursor ON audit(source_id,id DESC);
                CREATE INDEX IF NOT EXISTS audit_request_cursor ON audit(deal_id,id DESC);
                ALTER TABLE attestations ADD COLUMN IF NOT EXISTS binding_hash TEXT;
                ALTER TABLE attestations ADD COLUMN IF NOT EXISTS expires_at BIGINT;
                CREATE TABLE IF NOT EXISTS source_configs (id TEXT PRIMARY KEY, data JSONB NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_configs (id TEXT PRIMARY KEY, data JSONB NOT NULL);
                CREATE TABLE IF NOT EXISTS process_configs (id TEXT PRIMARY KEY, data JSONB NOT NULL);
                INSERT INTO process_configs(id,data)
                    SELECT id,jsonb_build_object('processId',id,'name',data->>'name',
                        'requirements',data->'requirements',
                        'contextSchema',COALESCE(data->'contextSchema','{"type":"object"}'::jsonb),
                        'enabled',true)
                    FROM workflow_configs WHERE data ? 'requirements'
                    ON CONFLICT(id) DO NOTHING;
                UPDATE workflow_configs SET data=(data-'requirements'-'contextSchema') || jsonb_build_object('processId',id)
                    WHERE data ? 'requirements';
                CREATE TABLE IF NOT EXISTS configuration_audit (
                    id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                    previous JSONB, current JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
            """)

    @contextmanager
    def transaction(self, deal_id: str):
        """Serialize evaluation for a deal across gateway workers.

        Args:
            deal_id: Deal identifier used for a transaction advisory lock.

        Yields:
            Transactional database connection.
        """
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (deal_id,))
            yield db

    def put_deal(self, deal: dict):
        """Register immutable local deal metadata.

        Args:
            deal: Validated deal fields.
        """
        with psycopg.connect(self.dsn) as db:
            db.execute("INSERT INTO deals VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",
                       (deal["dealId"], json.dumps(deal)))

    def audit(self, db, deal_id: str, source: str, fact: str, raw: dict, accepted: bool, reason: str, details: dict | None = None):
        """Record a source attempt and its validation result.

        Args:
            db: Active transaction.
            deal_id: Requested deal identifier.
            source: Requested source identifier.
            fact: Requested fact type.
            raw: Original response or transport error.
            accepted: Whether evidence passed validation.
            reason: Machine-readable result or failure reason.
            details: Transport, validation stages and business rule outcome.
        """
        try:
            serialized = json.dumps(raw, allow_nan=False)
        except (ValueError, TypeError):
            serialized = json.dumps({"invalidJson": repr(raw)})
        db.execute("""INSERT INTO audit(deal_id,source_id,fact_type,raw,accepted,reason,details)
                      VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)""",
                   (deal_id, source, fact, serialized, accepted, reason, json.dumps(details or {}, allow_nan=False)))

    def history(self, deal_id: str) -> list:
        """Read evidence audit in insertion order.

        Args:
            deal_id: Requested deal.

        Returns:
            Accepted and rejected source attempts.
        """
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            return db.execute("SELECT * FROM audit WHERE deal_id=%s ORDER BY id", (deal_id,)).fetchall()


    def configurations(self, kind: str) -> list[dict]:
        """Read persisted source or workflow definitions.

        Args:
            kind: Source or workflow catalogue.

        Returns:
            Ordered configuration objects.
        """
        from psycopg import sql
        table = {"source": "source_configs", "workflow": "workflow_configs", "process": "process_configs"}[kind]
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            rows = db.execute(sql.SQL("SELECT data FROM {} ORDER BY id").format(sql.Identifier(table))).fetchall()
        return [row["data"] for row in rows]

    def configure(self, kind: str, identifier: str, data: dict, seed: bool = False):
        """Persist an audited definition without overwriting operator edits on boot.

        Args:
            kind: Source or workflow catalogue.
            identifier: Stable configuration identifier.
            data: Validated definition.
            seed: Insert only when the identifier has never been registered.
        """
        from psycopg import sql
        table = {"source": "source_configs", "workflow": "workflow_configs", "process": "process_configs"}[kind]
        with self.transaction("configuration") as db:
            previous = db.execute(sql.SQL("SELECT data FROM {} WHERE id=%s").format(sql.Identifier(table)), (identifier,)).fetchone()
            if seed and previous:
                return
            db.execute(sql.SQL("INSERT INTO {} VALUES (%s,%s::jsonb) ON CONFLICT(id) DO UPDATE SET data=excluded.data").format(sql.Identifier(table)),
                       (identifier, json.dumps(data, allow_nan=False)))
            db.execute("INSERT INTO configuration_audit(kind,entity_id,previous,current) VALUES (%s,%s,%s::jsonb,%s::jsonb)",
                       (kind, identifier, json.dumps(previous["data"]) if previous else None, json.dumps(data, allow_nan=False)))

    def configure_profiles(self, process: dict, integration: dict, seed: bool = False):
        """Persist a legacy combined profile as two audited records atomically.

        Args:
            process: Validated business definition.
            integration: Validated technical definition.
            seed: Preserve existing records during bootstrap.
        """
        with self.transaction("configuration") as db:
            for table, kind, identifier, data in (
                ("process_configs", "process", process["processId"], process),
                ("workflow_configs", "workflow", integration["workflowId"], integration),
            ):
                from psycopg import sql
                previous = db.execute(sql.SQL("SELECT data FROM {} WHERE id=%s").format(sql.Identifier(table)), (identifier,)).fetchone()
                if seed and previous:
                    continue
                db.execute(sql.SQL("INSERT INTO {} VALUES (%s,%s::jsonb) ON CONFLICT(id) DO UPDATE SET data=excluded.data").format(sql.Identifier(table)),
                           (identifier, json.dumps(data, allow_nan=False)))
                db.execute("INSERT INTO configuration_audit(kind,entity_id,previous,current) VALUES (%s,%s,%s::jsonb,%s::jsonb)",
                           (kind, identifier, json.dumps(previous["data"]) if previous else None, json.dumps(data, allow_nan=False)))

    def requests(self) -> list[dict]:
        """Read registered requests with their latest evaluation decisions.

        Returns:
            Immutable request context and current backend decision.
        """
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            return db.execute("""SELECT d.id, d.data, e.result FROM deals d LEFT JOIN LATERAL
                (SELECT result FROM evaluations WHERE deal_id=d.id ORDER BY id DESC LIMIT 1) e ON true ORDER BY d.id""").fetchall()

    def source_history(self, source_id: str) -> list[dict]:
        """Read recent evidence and configuration audit for a source.

        Args:
            source_id: Registered source identifier.

        Returns:
            Up to 100 most recent evidence attempts.
        """
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            return db.execute("SELECT * FROM audit WHERE source_id=%s ORDER BY id DESC LIMIT 100", (source_id,)).fetchall()


    def responses(self, source_id: str | None = None, request_id: str | None = None,
                  fact_type: str | None = None, before: int | None = None, limit: int = 50) -> dict:
        """Read source response history with stable cursor pagination.

        Args:
            source_id: Optional source filter.
            request_id: Optional request filter.
            fact_type: Optional requested fact filter.
            before: Exclusive audit identifier cursor.
            limit: Maximum records returned.

        Returns:
            Response records, total matching count and next cursor.
        """
        from psycopg import sql
        conditions = [sql.SQL("(a.details->>'kind'='source' OR (a.details->>'kind' IS NULL AND a.source_id NOT IN ('gateway','registry')))")]
        params = []
        for column, value in (("source_id", source_id), ("deal_id", request_id), ("fact_type", fact_type)):
            if value is not None:
                conditions.append(sql.SQL("a.{}=%s").format(sql.Identifier(column)))
                params.append(value)
        where = sql.SQL(" AND ").join(conditions)
        with psycopg.connect(self.dsn, row_factory=dict_row) as db:
            total = db.execute(sql.SQL("SELECT count(*) AS count FROM audit a WHERE {}").format(where), params).fetchone()["count"]
            page_where = where
            page_params = list(params)
            if before is not None:
                page_where += sql.SQL(" AND a.id<%s")
                page_params.append(before)
            page_params.append(limit + 1)
            rows = db.execute(sql.SQL("""SELECT a.*,d.data->>'subjectRef' AS subject_ref
                FROM audit a LEFT JOIN deals d ON d.id=a.deal_id WHERE {} ORDER BY a.id DESC LIMIT %s""").format(page_where), page_params).fetchall()
        return {"items": rows[:limit], "total": total,
                "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None}
