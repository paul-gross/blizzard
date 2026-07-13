import { defineConfig } from '@hey-api/openapi-ts';

// Generated TypeScript client for the hub HTTP API — never hand-written fetch
// code (D-100, bzh:generated-client). Input is the committed OpenAPI spec the
// Python side exports (`uv run blizzard-export-openapi --out-dir openapi`); the
// generated output under projects/fleet/src/lib/api/hub is committed too, so the
// CI drift check can regenerate-and-diff. openapi-ts 0.99 cannot fan one config
// over two specs (its array form is broken), so the runner spec has its own
// sibling config and `npm run generate-client` runs both.
export default defineConfig({
  input: '../openapi/hub.openapi.json',
  output: { path: 'projects/fleet/src/lib/api/hub' },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
});
