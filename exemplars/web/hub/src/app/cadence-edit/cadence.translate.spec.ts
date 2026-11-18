// exemplar: bzh:frontend-domain-boundary, bzh:frontend-edit-baseline — the translator asserted at both edges.

import { gardenCadenceView } from 'fleet/testing';
import { editCadence } from './cadence.builders';
import { fromCadenceView, toEditBaseline, toEditRequest } from './cadence.translate';

describe('cadence translate', () => {
  it('reads an anchor this build does not know as its raw value, and opens it unstated', () => {
    const cadence = fromCadenceView(gardenCadenceView({ slug: 'evening', anchor: 'midnight' as never }));

    expect(cadence.anchor).toEqual({ unlisted: 'midnight' });
    expect(toEditBaseline(cadence).draft.anchor).toBe('unstated');
  });

  it('captures the id, the revision, and the draft from one read', () => {
    const baseline = toEditBaseline(editCadence({ revision: 4, spacingMinutes: 90 }));

    expect(baseline).toEqual({
      id: 'cad_evening',
      revision: 4,
      draft: { name: 'evening', cron: '0 20 * * *', zone: 'America/Chicago', spacing: '90m', anchor: 'unstated' },
    });
  });

  it('sends only what changed, the schedule as a pair', () => {
    const { draft } = toEditBaseline(editCadence());

    expect(toEditRequest(draft, draft)).toEqual({});
    expect(toEditRequest(draft, { ...draft, cron: '0 21 * * *' })).toEqual({
      schedule: { cron: '0 21 * * *', zone: 'America/Chicago' },
    });
    expect(toEditRequest(draft, { ...draft, cron: '', zone: '', spacing: '' })).toEqual({ schedule: null, spacing: null });
  });
});
