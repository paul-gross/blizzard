// exemplar: stand-in for `entries/hub-api.ts` — the real entry plus the exports a permits feature would add to it.

export * from './entries/hub-api';
export {
  getPermitApiPermitsPermitIdGet,
  grantPermitApiPermitsPermitIdGrantPost,
  listPermitsApiPermitsGet,
  resumePermitApiPermitsPermitIdResumePost,
  revokePermitApiPermitsPermitIdRevokePost,
  suspendPermitApiPermitsPermitIdSuspendPost,
} from './lib/api/hub/sdk.gen';
export { PermitState, PermitVerb, type PermitListResponse, type PermitView } from './lib/api/hub/types.gen';
