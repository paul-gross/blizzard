import type { Urgency } from 'fleet';
import { bannerUrgencyIcons, resolveUrgency, urgencyPill } from './urgencies.model';

const urgent = { id: 'u', slug: 'urgent', default: false, retired: true } as Urgency;
const normal = { id: 'n', slug: 'normal', default: true } as Urgency;
const ref = { id: 'u', slug: 'old-slug' };

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
    expect(urgencyPill({ urgency: { id: 'n', slug: 'normal' }, terminal: false }, [normal])).toBeNull();
    expect(urgencyPill({ urgency: null, terminal: false }, [normal])).toBeNull();
  });
  it('collects each banner urgency image mark once, skipping emoji, iconless, default and terminal banners', () => {
    const imaged = { id: 'i', slug: 'imaged', default: false, emoji: null, icon: { digest: 'd1', mediaType: 'image/png' } } as Urgency;
    const emoji = { ...imaged, id: 'e', emoji: '🔥' } as Urgency;
    const plain = { ...imaged, id: 'p', icon: null } as Urgency;
    const all = [imaged, emoji, plain, { ...normal, emoji: null, icon: { digest: 'dn', mediaType: 'image/png' } } as Urgency];
    const at = (id: string, terminal = false) => ({ urgency: { id: id, slug: id }, terminal });
    expect(bannerUrgencyIcons([at('i'), at('i'), at('e'), at('p'), at('n'), at('i', true), { urgency: null, terminal: false }], all)).toEqual([{ key: 'i', digest: 'd1' }]);
    expect(bannerUrgencyIcons([at('i')], undefined)).toEqual([]);
  });
});
