// Use an empty URL for a same-origin /api/v1 reverse proxy.
// For a separate API origin, configure its CORS policy to allow this frontend.
window.TRUST_CONFIG = { apiBaseUrl: '', requestTimeoutMs: 15000, testWalletUrl: 'https://wallet.interledger-test.dev/' };
