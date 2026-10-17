CREATE TABLE grant_sessions (
 id uuid PRIMARY KEY,
 data_cipher bytea NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE resource_sessions (
 resource_id uuid PRIMARY KEY REFERENCES resources(id),
 session_id uuid NOT NULL REFERENCES grant_sessions(id)
);
CREATE TABLE grant_attempts (
 id uuid PRIMARY KEY,
 resource_id uuid NOT NULL REFERENCES resources(id),
 session_id uuid NOT NULL REFERENCES grant_sessions(id),
 return_url text NOT NULL DEFAULT '',
 expires_at timestamptz NOT NULL,
 used_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX grant_attempt_expiry ON grant_attempts(expires_at) WHERE used_at IS NULL;
CREATE TABLE jobs (
 id uuid PRIMARY KEY,
 seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 resource_id uuid NOT NULL REFERENCES resources(id),
 grant_id uuid,
 kind text NOT NULL,
 data_cipher bytea NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done')),
 available_at timestamptz NOT NULL DEFAULT now(),
 locked_until timestamptz,
 owner uuid,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_ready ON jobs(available_at,seq) WHERE status='queued';
