/*
 * `fleet`'s test-helper entrypoint (issue #81) — a **second**, tsconfig
 * path-mapped barrel (`fleet/testing`, `web/tsconfig.json`), deliberately
 * separate from `fleet`'s production `public-api.ts` so a test helper never
 * reaches a production bundle. Both `fleet`'s own specs and `local-panel`'s
 * import from here rather than keeping their own copies.
 */

export { settle } from './settle';
export {
  stubRequestClient,
  stubError,
  type RequestClientStub,
  type CapturedRequest,
  type StubHttpError,
} from './stub-request-client';
