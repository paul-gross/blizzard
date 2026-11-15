import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { chunkControlStub, chunkDetail } from 'fleet/testing';
import type { UrgencyView } from '../../api/hub';
import { legibleOnDark, projectColorValue } from '../../kit/kit-swatch-color';
import { CHUNK_URGENCY_ICON, ChunkUrgency } from './chunk-urgency';

const normal = { urgency_id: 'normal', slug: 'normal', name: 'Normal', default: true, retired: false, place: 2, color: 'blue', emoji: null, icon: null } as UrgencyView;
const urgent = { ...normal, urgency_id: 'urgent', slug: 'urgent', name: 'Urgent', default: false, emoji: '🔥' } as UrgencyView;

describe('ChunkUrgency', () => {
  beforeEach(() => TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection(), { provide: CHUNK_URGENCY_ICON, useValue: () => signal('data:image/png;base64,YQ==') }],
  }));

  async function mount(id: string, terminal = false) {
    const fixture = TestBed.createComponent(ChunkUrgency);
    fixture.componentRef.setInput('detail', chunkDetail({ terminal, urgency: { urgency_id: id, slug: id } }));
    fixture.componentRef.setInput('control', { ...chunkControlStub(), urgency: { values: signal([normal, urgent]), pending: () => null, set: vi.fn() } });
    await fixture.whenStable();
    return fixture;
  }

  it.each(['normal', 'urgent'])('renders %s as plain read-only detail with a separator', async (id) => {
    const fixture = await mount(id);
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('[data-testid="chunk-urgency"]')?.textContent).toBe(id === 'normal' ? 'Normal' : 'Urgent');
    expect(root.querySelector('.sep')?.textContent).toBe('·');
    expect(root.querySelector('button, img, .mark, [data-testid="chunk-urgency-menu"]')).toBeNull();
  });

  it('uses the historical slug for an unresolved terminal urgency', async () => {
    const fixture = await mount('former', true);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="chunk-urgency"]')?.textContent).toBe('former');
  });

  it('renders the banner name and optional emoji or image without an editor', async () => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('banner', true);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('.banner-label')?.textContent).toBe('Urgent');
    expect(root.querySelector('.mark')?.textContent).toBe('🔥');
    fixture.componentRef.setInput('control', null);
    fixture.componentRef.setInput('resolvedUrgency', { ...urgent, emoji: null, icon: { digest: 'd', media_type: 'image/png' } });
    await fixture.whenStable();
    expect(root.querySelector('img.mark')?.getAttribute('src')).toContain('data:image/png');
    expect(root.querySelector('button')).toBeNull();
  });

  it.each(['#000000', 'red', 'green'])('uses legible banner ink for %s', async (color) => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('control', null);
    fixture.componentRef.setInput('resolvedUrgency', { ...urgent, color });
    fixture.componentRef.setInput('banner', true);
    await fixture.whenStable();
    const banner = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('.banner')!;
    expect(banner.style.getPropertyValue('--urgency-color')).toBe(color.startsWith('#') ? legibleOnDark(color) : projectColorValue(color));
  });
});
