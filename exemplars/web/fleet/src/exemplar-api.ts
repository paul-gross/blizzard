// exemplar: stand-in for `public-api.ts` — the real barrel plus the exports a permits feature would add to it.

export * from './public-api';
export { hubPermitKey, hubPermitsKey } from './lib/core/query-keys';
export {
  getPermitApiPermitsPermitIdGet,
  grantPermitApiPermitsPermitIdGrantPost,
  listPermitsApiPermitsGet,
  resumePermitApiPermitsPermitIdResumePost,
  revokePermitApiPermitsPermitIdRevokePost,
  suspendPermitApiPermitsPermitIdSuspendPost,
} from './lib/api/hub/sdk.gen';
export { PermitState, PermitVerb, type PermitListResponse, type PermitView } from './lib/api/hub/types.gen';
