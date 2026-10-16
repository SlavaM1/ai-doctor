# AI Doctor console

React + TypeScript + Vite. No sample data, provider keys, or direct runner access.

```sh
npm ci
npm run dev
npm test
npm run build
```

Development requests to `/api` proxy to `http://127.0.0.1:8000`. Production nginx strips `/api/` and proxies to `http://api:8000/`. Serve the frontend and API on the same origin; SSE buffering is disabled. The container listens on port 80 and exposes `/health`. Bind published ports to localhost in the integrating compose configuration.

All mutations include `X-Doctor-Action: 1`. Models come from `/models` or the public `/settings` response. Empty model selections are sent unchanged so the backend owns default behavior.

Deployment approval requires `FixJob.status === "READY"`, a nonempty patch and SHA-256 `patch_sha`, and `tests.passed === true` with tests neither skipped nor timed out. If test hash/base metadata is present, it must match the displayed fix. The confirmation dialog snapshots and displays the full hash, requires a review checkbox, and sends `{confirmed: true, patch_sha}`. The backend must independently revalidate the hash, test results, and deployment policy. The console never deploys on fix preparation.

SSE default messages trigger debounced data refreshes; periodic 15-second reconciliation covers missed events and reconnection. API errors remain visible without inventing fallback incidents or metrics.
