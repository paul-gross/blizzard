import { defineConfig } from '@hey-api/openapi-ts';
import { responseUnionInput } from './scripts/openapi-response-union.mjs';

// Generated TypeScript client for the runner local API — sibling of
// openapi-ts.hub.config.ts (see it for why the two specs need two configs).
export default defineConfig({
  input: '../openapi/runner.openapi.json',
  parser: { patch: responseUnionInput },
  output: { path: 'projects/fleet/src/lib/api/runner' },
  plugins: ['@hey-api/client-fetch', { name: '@hey-api/typescript', enums: 'javascript' }, '@hey-api/sdk'],
});
