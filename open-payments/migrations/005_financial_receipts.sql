-- Response cache expiry must never permit a second financial intent with the
-- same method/path/key. Keep successful financial responses independently.
CREATE TABLE financial_receipts (
 scope text NOT NULL,
 key text NOT NULL,
 request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
 status integer NOT NULL CHECK (status BETWEEN 200 AND 299),
 response_body bytea NOT NULL,
 response_headers jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (scope,key)
);

-- Preserve all receipts still present when upgrading, including expired rows
-- not yet removed by maintenance. Already-deleted history requires reconciliation.
INSERT INTO financial_receipts(scope,key,request_hash,status,response_body,response_headers,created_at)
 SELECT scope,key,request_hash,status,response_body,response_headers,created_at
 FROM idempotency_records
 WHERE status BETWEEN 200 AND 299 AND
 (scope IN ('POST /v1/payments','POST /v1/transfers','POST /v1/sim/puntored/qr-payments')
  OR scope LIKE 'POST /qr/%/pay');
