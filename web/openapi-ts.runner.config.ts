import { defineConfig } from '@hey-api/openapi-ts';

// Generated TypeScript client for the runner local API — sibling of
// openapi-ts.hub.config.ts (see it for why the two specs need two configs).
export default defineConfig({
  input: '../openapi/runner.openapi.json',
  output: { path: 'projects/fleet/src/lib/api/runner' },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
});
