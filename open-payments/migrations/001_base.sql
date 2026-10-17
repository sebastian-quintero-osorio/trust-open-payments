CREATE TABLE IF NOT EXISTS idempotency_records (
 scope text NOT NULL,
 key text NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
 request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
 status integer NOT NULL CHECK (status BETWEEN 200 AND 299 OR status IN (400,404,409,422)),
 response_body bytea NOT NULL,
 response_headers jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now() + interval '72 hours',
 PRIMARY KEY (scope,key)
);
CREATE INDEX IF NOT EXISTS idempotency_expiry ON idempotency_records(expires_at);
-- Resource payloads contain only the public contract. Credentials belong in a
-- separate encrypted grant repository introduced with authorization in phase 3.
CREATE TABLE IF NOT EXISTS resources (
 id uuid PRIMARY KEY,
 kind text NOT NULL CHECK (kind IN ('collection_account','pool_wallet','transfer','payment','sim_payment')),
 external_ref text,
 status text NOT NULL,
 payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS resources_external_ref ON resources(kind,external_ref,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_collection_per_ref
 ON resources(external_ref) WHERE kind='collection_account' AND status='active';
