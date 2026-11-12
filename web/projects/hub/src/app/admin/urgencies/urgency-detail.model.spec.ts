import type { UrgencyView } from 'fleet';
import { urgencyActions, urgencyCliCommand, urgencyDestinations, urgencyFormValues, urgencyRecordVm } from './urgency-detail.model';

const urgency: UrgencyView = {
  urgency_id: 'u', slug: 'soon', name: 'Soon', color: 'red', emoji: '🔥', icon: null,
  retired: false, default: false, revision: 2, open_chunks: 3, place: 0, created_at: '', created_by: 'operator',
};
const image = { ...urgency, emoji: null, icon: { digest: 'abc', media_type: 'image/png' } };

describe('urgency detail model', () => {
  it('maps a record and its default, retired and count facts', () => {
    expect(urgencyRecordVm(undefined)).toBeNull();
    expect(urgencyRecordVm({ ...urgency, default: true, retired: true })).toMatchObject({
      name: 'Soon', revision: 2, note: 'Retired', hasHistory: true,
      facts: [{ label: 'Slug', value: 'soon' }, { label: 'Color', value: 'red' },
        { label: 'Open chunks', value: '3' }, { label: 'Default', value: 'Yes' }],
    });
    expect(urgencyRecordVm(urgency)?.note).toBeNull();
    expect(urgencyRecordVm(urgency)?.facts[3].value).toBe('No');
  });
  it('waits for an authenticated image and preserves the chosen mark', () => {
    expect(urgencyFormValues(undefined, null)).toBeNull();
    expect(urgencyFormValues(image, undefined)).toBeNull();
    expect(urgencyFormValues(image, 'data:image/png;base64,YQ==')?.['mark']).toBe('data:image/png;base64,YQ==');
    expect(urgencyFormValues(urgency, null)?.['mark']).toBe('🔥');
    expect(urgencyFormValues({ ...urgency, emoji: null }, null)?.['mark']).toBe('');
  });
  it('gates actions by viewport and permissions, default, in-use and image readiness', () => {
    expect(urgencyActions('mobile', true, true, urgency, {})).toBeNull();
    expect(urgencyActions('desktop', false, true, urgency, {})).toBeNull();
    expect(urgencyActions('desktop', true, true, undefined, {})).toBeNull();
    expect(urgencyActions('desktop', true, false, urgency, null)).toMatchObject({ edit: false, lifecycle: 'retire', retireBlocked: 'Moving open chunks requires chunk control.' });
    expect(urgencyActions('desktop', true, true, { ...urgency, default: true }, {})).toMatchObject({ edit: true, retireBlocked: 'Choose another default before retiring this urgency.' });
    expect(urgencyActions('desktop', true, true, { ...urgency, retired: true, open_chunks: 0 }, {})).toMatchObject({ edit: true, lifecycle: 'enable', retireBlocked: null });
    expect(urgencyActions('desktop', true, false, { ...urgency, open_chunks: 0 }, {})?.retireBlocked).toBeNull();
  });
  it('offers only other live destinations in their original order', () => {
    const other = { ...urgency, urgency_id: 'other' };
    const retired = { ...urgency, urgency_id: 'retired', retired: true };
    expect(urgencyDestinations([urgency, retired, other], 'u')).toEqual([other]);
    expect(urgencyDestinations([urgency, retired, other], undefined)).toEqual([urgency, other]);
  });
  it('names the CLI edit command only for a phone with a record', () => {
    expect(urgencyCliCommand('desktop', urgency)).toBeNull();
    expect(urgencyCliCommand('mobile', undefined)).toBeNull();
    expect(urgencyCliCommand('mobile', urgency)).toBe('blizzard hub urgency edit soon');
  });
});
