import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { chunkControlStub, domainChunkDetail as chunkDetail } from 'fleet/testing';
import type { Urgency } from '../../core/urgency/urgency.domain';
import { legibleOnDark, projectColorValue } from '../../kit/kit-swatch-color';
import { ChunkUrgency } from './chunk-urgency';

const normal = { id: 'normal', slug: 'normal', name: 'Normal', default: true, retired: false, place: 2, color: 'blue', emoji: null, icon: null } as Urgency;
const urgent = { ...normal, id: 'urgent', slug: 'urgent', name: 'Urgent', default: false, emoji: '🔥' } as Urgency;

describe('ChunkUrgency', () => {
  beforeEach(() => TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection()],
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

  it('shows the reference slug read-only when the runner has no urgency catalog', async () => {
    const fixture = await mount('standard');
    fixture.componentRef.setInput('control', null);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('[data-testid="chunk-urgency"]')?.textContent).toBe('standard');
    expect(root.querySelector('.sep')?.textContent).toBe('·');
    expect(root.querySelector('button, img, .mark')).toBeNull();
  });

  it('renders no urgency when the reference is absent', async () => {
    const fixture = TestBed.createComponent(ChunkUrgency);
    fixture.componentRef.setInput('detail', chunkDetail({ urgency: null }));
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="chunk-urgency"]')).toBeNull();
  });

  it('renders the banner name and emoji without an editor', async () => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('banner', true);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('.banner-label')?.textContent).toBe('Urgent');
    expect(root.querySelector('.mark')?.textContent).toBe('🔥');
    expect(root.querySelector('button')).toBeNull();
  });

  it('lets the emoji win over a supplied icon URL', async () => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('banner', true);
    fixture.componentRef.setInput('iconUrl', 'blob:icon');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('.mark')?.textContent).toBe('🔥');
  });

  it('renders the iconUrl input only in banner mode with no emoji', async () => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('control', null);
    fixture.componentRef.setInput('resolvedUrgency', { ...urgent, emoji: null, icon: { digest: 'd', mediaType: 'image/png' } });
    fixture.componentRef.setInput('iconUrl', 'blob:icon');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('img')).toBeNull();
    fixture.componentRef.setInput('banner', true);
    await fixture.whenStable();
    expect(root.querySelector('img.mark')?.getAttribute('src')).toBe('blob:icon');
  });

  it('renders no image by default', async () => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('control', null);
    fixture.componentRef.setInput('resolvedUrgency', { ...urgent, emoji: null, icon: { digest: 'd', mediaType: 'image/png' } });
    fixture.componentRef.setInput('banner', true);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('.banner-label')?.textContent).toBe('Urgent');
  });

  it('marks a retired urgency in the detail', async () => {
    const fixture = await mount('urgent');
    fixture.componentRef.setInput('control', null);
    fixture.componentRef.setInput('resolvedUrgency', { ...urgent, retired: true });
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="chunk-urgency"]')?.textContent?.trim()).toBe('Urgent (retired)');
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
