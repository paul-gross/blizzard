import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test, type Wire } from '../../harness/fixtures';

// A version-2 export kept from a hub that still took the address fields — two forges, one with no web origin, and
// records on both, some first written through those fields — applies to a running hub unrefused and unwarned, and
// exports with current defaults, beside the tenant's own `standard` urgency. It declares the tenant's own project, so it owns its tenant.
test.use({ isolation: 'fresh-tenant' });

const KEPT = path.join(__dirname, '..', '..', 'fixtures', 'transition_era_export_v2.json');

test('test_a_kept_transition_era_export_applies_cleanly_and_exports_with_defaults', async ({ api }) => {
  const kept = JSON.parse(fs.readFileSync(KEPT, 'utf8')) as Wire.ConfigDocumentV2;
  for (const name of kept.secrets ?? []) {
    expect((await api.post('/api/secrets', { data: { name, value: 'kept-token' } })).status).toBe(201);
  }
  const applied = await api.post<Wire.ConfigApplyResponse>('/api/config/apply', { data: kept });
  expect(applied.status, JSON.stringify(applied.body)).toBe(200);
  expect(applied.body.warnings).toEqual([]);
  const exported = await api.get<Wire.ConfigDocumentV2>('/api/config/export');
  expect(exported.status).toBe(200);
  // The export also carries the sections a kept document predates: the tenant's seeded `standard` urgency, its
  // order, and the default it names. Repositories gain the current default for allow_no_ci.
  const { urgencies, urgency_order: order, settings, ...rest } = exported.body;
  expect(urgencies?.map((u) => u.slug)).toEqual(['standard']);
  expect(order).toEqual(['standard']);
  expect(settings?.default_urgency).toBe('standard');
  expect(rest).toEqual({
    ...kept,
    repositories: kept.repositories?.map((repository) => ({ ...repository, allow_no_ci: false })),
  });
});
