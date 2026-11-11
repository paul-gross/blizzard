import type { UrgencyView } from 'fleet';
import { resolveUrgency, urgencyPill } from './urgencies.model';

const urgent = { urgency_id: 'u', slug: 'urgent', default: false, retired: true } as UrgencyView;
const normal = { urgency_id: 'n', slug: 'normal', default: true } as UrgencyView;
const ref = { urgency_id: 'u', slug: 'old-slug' };

describe('urgency presentation', () => {
  it('resolves by id, even for a retired record or renamed slug', () => {
    expect(resolveUrgency(ref, [urgent])).toBe(urgent);
    expect(resolveUrgency(null, [urgent])).toBeNull();
    expect(resolveUrgency(ref, undefined)).toBeNull();
    expect(resolveUrgency(ref, [normal])).toBeNull();
  });
  it('shows only a resolved non-default urgency on an unfinished chunk', () => {
    expect(urgencyPill({ urgency: ref, terminal: false }, [urgent])).toBe(urgent);
    expect(urgencyPill({ urgency: ref, terminal: true }, [urgent])).toBeNull();
    expect(urgencyPill({ urgency: { urgency_id: 'n', slug: 'normal' }, terminal: false }, [normal])).toBeNull();
    expect(urgencyPill({ urgency: null, terminal: false }, [normal])).toBeNull();
  });
});
