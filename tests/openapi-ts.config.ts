import { defineConfig } from '@hey-api/openapi-ts';

// Types only, not a client: the harness's clients make the calls. The output is committed and the static job
// regenerates it and fails on drift, so a renamed field in the hub's OpenAPI document breaks `tsc` first.
export default defineConfig({
  input: '../openapi/hub.openapi.json',
  output: { path: 'harness/client/wire/hub' },
  plugins: ['@hey-api/typescript'],
});
