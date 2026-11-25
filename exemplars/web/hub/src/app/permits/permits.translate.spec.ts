// exemplar: bzh:frontend-domain-boundary, bzh:frontend-open-vocabulary.

import { type PermitState, type PermitVerb } from 'fleet';
import { permitWire } from './permits.fixtures.translate';
import { fromPermitView } from './permits.translate';

it('translates known values and preserves unknown state while omitting unknown verbs', () => {
  const wire = permitWire({
    permit_id: 'pmt_1', project_id: null, project_slug: null, project_name: null,
    holder: 'ada', capability: 'deploy', state: 'lapsed' as PermitState,
    verbs: ['grant', 'renew' as PermitVerb], requested_at: '2026-10-01T09:00:00Z',
    requested_by: 'ada', changed_at: null, changed_by: null, revocation_note: null,
  });
  expect(fromPermitView(wire)).toEqual({ ...wire, state: { unlisted: 'lapsed' }, verbs: ['grant'] });
  expect(fromPermitView({ ...wire, state: 'granted' }).state).toBe('granted');
});
