/*
 * `fleet`'s test-helper entrypoint — a **second**, tsconfig
 * path-mapped barrel (`fleet/testing`, `web/tsconfig.json`), deliberately
 * separate from `fleet`'s production `public-api.ts` so a test helper never
 * reaches a production bundle.
 */

export { settle } from './settle';
export { hiddenAtContainerWidth, resolveContainerStyle } from './container-query';
export {
  stubRequestClient,
  stubError,
  type RequestClientStub,
  type CapturedRequest,
  type StubHttpError,
} from './stub-request-client';
export { OPERATOR_ME_RESPONSE } from './auth-fixtures';
