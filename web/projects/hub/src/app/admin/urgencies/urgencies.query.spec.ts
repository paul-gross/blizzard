import type { UrgencyView } from 'fleet';
import { domainUrgencyQueryOptions } from './urgencies.query';

it('projects the detail over the existing cache and polling options', () => {
  const wire: UrgencyView = { urgency_id: 'u', slug: 's', name: 'n', color: 'red', emoji: null, icon: null,
    retired: false, default: true, place: 0, revision: 2, open_chunks: 4, created_at: 't', created_by: 'a' };
  const domain = domainUrgencyQueryOptions('u');
  expect(domain.refetchInterval).toBe(15000);
  expect(domain.enabled).toBe(true);
  expect(domain.select(wire)).toMatchObject({ id: 'u', default: true, openChunks: 4 });
  expect(domainUrgencyQueryOptions(null).enabled).toBe(false);
});
