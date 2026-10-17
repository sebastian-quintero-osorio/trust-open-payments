CREATE TABLE pool_wallets (
 resource_id uuid PRIMARY KEY REFERENCES resources(id),
 wallet_address text NOT NULL UNIQUE,
 role text NOT NULL CHECK (role IN ('collection','sim_payer')),
 asset_code text NOT NULL,
 asset_scale integer NOT NULL CHECK (asset_scale BETWEEN 0 AND 255),
 discovery jsonb NOT NULL
);
CREATE TABLE collection_wallets (
 account_id uuid PRIMARY KEY REFERENCES resources(id),
 pool_id uuid NOT NULL REFERENCES pool_wallets(resource_id),
 UNIQUE(pool_id)
);
CREATE SEQUENCE sim_terminal_sequence;
CREATE TABLE sim_entries (
 resource_id uuid PRIMARY KEY REFERENCES resources(id),
 seq bigint UNIQUE,
 webhook_body bytea,
 webhook_delivered_at timestamptz
);
CREATE TABLE webhook_outbox (
 resource_id uuid PRIMARY KEY REFERENCES resources(id),
 event_id uuid NOT NULL UNIQUE,
 raw_body bytea NOT NULL,
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','delivered','exhausted'))
);
CREATE TABLE qr_forms (
 token uuid PRIMARY KEY,
 account_id uuid NOT NULL REFERENCES resources(id),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '15 minutes',
 used_at timestamptz
);
CREATE TABLE qr_rate_limits (
 ip_hash bytea NOT NULL,
 minute timestamptz NOT NULL,
 requests integer NOT NULL,
 PRIMARY KEY(ip_hash,minute)
);
CREATE TABLE resource_flows (
 resource_id uuid PRIMARY KEY REFERENCES resources(id),
 data_cipher bytea NOT NULL
);
