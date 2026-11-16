import type { UrgencyView } from 'fleet';
import { bannerUrgencyIcons, resolveUrgency, urgencyPill } from './urgencies.model';

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
  it('collects each banner urgency image mark once, skipping emoji, iconless, default and terminal banners', () => {
    const imaged = { urgency_id: 'i', slug: 'imaged', default: false, emoji: null, icon: { digest: 'd1', media_type: 'image/png' } } as UrgencyView;
    const emoji = { ...imaged, urgency_id: 'e', emoji: '🔥' } as UrgencyView;
    const plain = { ...imaged, urgency_id: 'p', icon: null } as UrgencyView;
    const all = [imaged, emoji, plain, { ...normal, emoji: null, icon: { digest: 'dn', media_type: 'image/png' } } as UrgencyView];
    const at = (id: string, terminal = false) => ({ urgency: { urgency_id: id, slug: id }, terminal });
    expect(bannerUrgencyIcons([at('i'), at('i'), at('e'), at('p'), at('n'), at('i', true), { urgency: null, terminal: false }], all)).toEqual([{ key: 'i', digest: 'd1' }]);
    expect(bannerUrgencyIcons([at('i')], undefined)).toEqual([]);
  });
});
