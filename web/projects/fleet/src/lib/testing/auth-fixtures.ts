import type { MeResponse } from '../api/hub';

/**
 * The full-permission identity `GET /api/me` resolves to under `auth.mode = "none"`
 * (issue #93's default-preserving fallback — see `hub/api/auth_session.py`'s
 * `IMPLICIT_OPERATOR`). Every spec that mounts the app root (or otherwise depends on
 * its session gate settling to `'ready'`) stubs `/api/me` with this, so pre-#93
 * chrome/route assertions keep exercising the "everything visible, no login"
 * behavior without asserting anything about auth itself — a spec that *does* want to
 * assert gating stubs its own narrower `MeResponse`/provider list instead.
 */
export const OPERATOR_ME_RESPONSE: MeResponse = {
  user_id: 'operator',
  username: 'operator',
  display_name: 'operator',
  role: 'superuser',
  permissions: [
    'fleet:view',
    'chunk:ingest',
    'chunk:control',
    'question:answer',
    'gate:resolve',
    'queue:reorder',
    'runner:pause',
    'graph:edit',
    'user:manage',
  ],
};
