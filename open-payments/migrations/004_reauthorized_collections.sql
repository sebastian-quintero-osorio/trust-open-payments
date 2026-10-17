-- Historical accounts retain their wallet link; only active assignments are unique.
ALTER TABLE collection_wallets ADD COLUMN active boolean NOT NULL DEFAULT true;
ALTER TABLE collection_wallets DROP CONSTRAINT collection_wallets_pool_id_key;
UPDATE collection_wallets SET active=false WHERE account_id IN (
 SELECT id FROM resources WHERE kind='collection_account' AND status='revoked'
);
CREATE UNIQUE INDEX one_active_account_per_wallet ON collection_wallets(pool_id) WHERE active;
