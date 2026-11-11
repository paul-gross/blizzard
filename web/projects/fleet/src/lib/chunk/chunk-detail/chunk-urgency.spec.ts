import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { chunkControlStub, chunkDetail } from 'fleet/testing';
import type { UrgencyView } from '../../api/hub';
import { ChunkUrgency } from './chunk-urgency';

const normal = { urgency_id: 'normal', slug: 'normal', name: 'Normal', default: true, retired: false, place: 2, color: 'blue', emoji: null, icon: null } as UrgencyView;
const urgent = { ...normal, urgency_id: 'urgent', slug: 'urgent', name: 'Urgent', default: false, place: 1, emoji: '🔥' } as UrgencyView;
const retired = { ...urgent, urgency_id: 'old', name: 'Old', retired: true, place: null } as UrgencyView;

describe('ChunkUrgency', () => {
  const pending = signal<string | null>(null);
  const values = signal<readonly UrgencyView[]>([normal, retired, urgent]);
  const set = vi.fn();
  const control = { ...chunkControlStub(), urgency: { values, pending: () => pending(), set } };
  beforeEach(() => {
    pending.set(null); set.mockReset(); control.canControl.set(true);
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  });
  async function mount(terminal = false, id = 'normal') {
    const fixture = TestBed.createComponent(ChunkUrgency);
    fixture.componentRef.setInput('detail', chunkDetail({ chunk_id: 'ch_1', terminal, urgency: { urgency_id: id, slug: id } }));
    fixture.componentRef.setInput('control', control);
    await fixture.whenStable();
    return fixture;
  }
  it('shows the default name and checks the ordered live choices; writes the chosen id', async () => {
    const fixture = await mount();
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="chunk-urgency-menu"]')!.click();
    await fixture.whenStable();
    const rows = [...document.querySelectorAll<HTMLElement>('[data-testid="chunk-urgency-options"] [role="menuitemradio"]')];
    expect(rows.map((e) => e.querySelector('.label')?.textContent?.trim())).toEqual(['Urgent', 'Normal']);
    expect(rows.map((e) => e.getAttribute('aria-checked'))).toEqual(['false', 'true']);
    rows[0].click();
    expect(set).toHaveBeenCalledWith('ch_1', 'urgent');
  });
  it('keeps the urgency menu as the only tab stop with an accessible label', async () => {
    const fixture = await mount(false, 'urgent');
    const root = fixture.nativeElement as HTMLElement;
    const trigger = root.querySelector<HTMLButtonElement>('[data-testid="chunk-urgency-menu"]')!;
    const pill = trigger.querySelector<HTMLElement>('.pill')!;
    expect(trigger.getAttribute('aria-label')).toBe('Change urgency: Urgent');
    expect(pill.hasAttribute('tabindex')).toBe(false);
    expect(pill.getAttribute('aria-hidden')).toBe('true');
    trigger.focus();
    expect(document.activeElement).toBe(trigger);
    trigger.click();
    await fixture.whenStable();
    expect(document.querySelector('[data-testid="chunk-urgency-options"]')).not.toBeNull();
  });
  it('renders pending urgency immediately and withholds edits without permission or for finished work', async () => {
    const fixture = await mount();
    pending.set('urgent');
    await fixture.whenStable();
    const pill = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('fleet-kit-urgency-pill .pill');
    expect(pill?.textContent?.trim()).toBe('🔥');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="chunk-urgency-menu"]')?.getAttribute('aria-label')).toBe('Change urgency: Urgent');
    expect((fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="chunk-urgency-menu"]')?.disabled).toBe(true);
    control.canControl.set(false);
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="chunk-urgency-menu"]')).toBeNull();
    fixture.componentRef.setInput('detail', chunkDetail({ chunk_id: 'ch_1', terminal: true, urgency: { urgency_id: 'old', slug: 'old' } }));
    pending.set(null);
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Old (retired)');
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="chunk-urgency-menu"]')).toBeNull();
  });
});
