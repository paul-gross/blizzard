import type { UrgencyView } from 'fleet';
import { liveUrgencies, urgencyOptions, visibleUrgencies } from './urgencies.model';

const first = { urgency_id: 'first', retired: false } as UrgencyView;
const second = { urgency_id: 'second', retired: false } as UrgencyView;
const retired = { urgency_id: 'retired', retired: true } as UrgencyView;

describe('visibleUrgencies', () => {
  it('filters lifecycle without changing the hub order', () => {
    expect(visibleUrgencies([second, first, retired], 'active')).toEqual([second, first]);
    expect(visibleUrgencies([second, first, retired], 'retired')).toEqual([retired]);
    expect(visibleUrgencies([second, first, retired], 'all')).toEqual([second, first, retired]);
  });
});

import { urgencyCreate, urgencyEdit, urgencyPosition } from './urgencies.model';

describe('urgency writes', () => {
  const image = 'data:image/png;base64,YWJj';
  it('creates one mark and omits an unchosen color', () => {
    expect(urgencyCreate({ slug: 'now', name: 'Now', color: '', mark: image })).toEqual({ slug: 'now', name: 'Now', icon: { media_type: 'image/png', data: 'YWJj' } });
    expect(urgencyCreate({ slug: 'now', name: 'Now', color: 'red', mark: '🔥' })).toEqual({ slug: 'now', name: 'Now', color: 'red', emoji: '🔥' });
  });
  it('preserves an unchanged image, and clears the other mark when switching', () => {
    const shown = { slug: 'now', name: 'Now', color: 'red', mark: image };
    expect(urgencyEdit(shown, { ...shown, name: 'Next', slug: 'ignored' })).toEqual({ name: 'Next' });
    expect(urgencyEdit(shown, { ...shown, mark: '🔥' })).toEqual({ emoji: '🔥', icon: null });
    expect(urgencyEdit({ ...shown, mark: '🔥' }, { ...shown, mark: image })).toEqual({ emoji: null, icon: { media_type: 'image/png', data: 'YWJj' } });
    expect(urgencyEdit(shown, { ...shown, mark: '' })).toEqual({ emoji: null, icon: null });
  });
  it('resolves one move to the new predecessor', () => {
    expect(urgencyPosition([first, second, retired], 0, 2)).toEqual({ urgency: 'first', after: 'retired' });
    expect(urgencyPosition([first, second, retired], 2, 0)).toEqual({ urgency: 'retired', after: null });
    expect(urgencyPosition([first, second], 1, 1)).toBeNull();
  });
});

describe('urgency choices', () => {
  it('keeps live records in order and derives their labels', () => {
    const a = { ...first, name: 'First' };
    const b = { ...second, name: 'Second' };
    expect(liveUrgencies([b, retired, a])).toEqual([b, a]);
    expect(urgencyOptions(liveUrgencies([b, retired, a]))).toEqual([
      { value: 'second', label: 'Second' }, { value: 'first', label: 'First' },
    ]);
    expect(liveUrgencies([])).toEqual([]);
  });
});
