import { defineConfig } from '@hey-api/openapi-ts';

// The hub client config (`openapi-ts.hub.config.ts`) pointed at the exemplar router instead of the hub
// spec: `scripts/exemplar-client.sh` supplies the exported spec and a scratch output, then holds the committed stand-in
// to the result.
export default defineConfig({
  input: process.env['EXEMPLAR_OPENAPI_INPUT']!,
  output: { path: process.env['EXEMPLAR_OPENAPI_OUTPUT']! },
  plugins: ['@hey-api/client-fetch', { name: '@hey-api/typescript', enums: 'javascript' }, '@hey-api/sdk'],
});
