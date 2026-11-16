import type { UrgencyView } from 'fleet';
import { urgencyOptions } from './urgency-options.model';

const live = { urgency_id: 'u1', slug: 'live', name: 'Live', retired: false } as UrgencyView;
const old = { urgency_id: 'u2', slug: 'old', name: 'Old', retired: true } as UrgencyView;
const both = [old, live];

describe('urgencyOptions', () => {
  it('keys each option by id or by slug, in arrival order', () => {
    expect(urgencyOptions(both, { key: 'urgency_id', retired: 'include' })).toEqual([
      { value: 'u2', label: 'Old' },
      { value: 'u1', label: 'Live' },
    ]);
    expect(urgencyOptions(both, { key: 'slug', retired: 'include' })).toEqual([
      { value: 'old', label: 'Old' },
      { value: 'live', label: 'Live' },
    ]);
  });
  it('excludes retired urgencies', () => {
    expect(urgencyOptions(both, { key: 'slug', retired: 'exclude' })).toEqual([{ value: 'live', label: 'Live' }]);
  });
  it('keeps a retired urgency only when it is the current value, by id or by slug', () => {
    const live1 = [{ value: 'live', label: 'Live' }];
    expect(urgencyOptions(both, { key: 'slug', retired: { current: null } })).toEqual(live1);
    expect(urgencyOptions(both, { key: 'slug', retired: { current: 'live' } })).toEqual(live1);
    expect(urgencyOptions(both, { key: 'slug', retired: { current: 'old' } })).toEqual([{ value: 'old', label: 'Old' }, ...live1]);
    expect(urgencyOptions(both, { key: 'slug', retired: { current: 'u2' } })).toEqual([{ value: 'old', label: 'Old' }, ...live1]);
  });
  it('leads with an inherit row, named or bare', () => {
    expect(urgencyOptions([live], { key: 'slug', retired: 'exclude', inherit: { label: 'Tenant default', name: 'Live' } })[0])
      .toEqual({ value: '', label: 'Tenant default (Live)' });
    expect(urgencyOptions([live], { key: 'slug', retired: 'exclude', inherit: { label: 'Routine default', name: null } }))
      .toEqual([{ value: '', label: 'Routine default' }, { value: 'live', label: 'Live' }]);
  });
});
