import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { QueuePanelView } from './queue-view';

const ENTRIES = [
  { chunk_id: 'ch_top', graph_id: 'gr_1', position: 0, pm_pointers: [{ source: 'widget', ref: '1' }] },
  { chunk_id: 'ch_mid', graph_id: 'gr_1', position: 1, pm_pointers: [{ source: 'widget', ref: '2' }] },
  { chunk_id: 'ch_low', graph_id: 'gr_1', position: 2, pm_pointers: [] },
];

describe('QueuePanelView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [QueuePanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one row per entry — off plain inputs alone', async () => {
    const fixture = TestBed.createComponent(QueuePanelView);
    fixture.componentRef.setInput('entries', ENTRIES);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="queue-row"]')).toHaveLength(3);
  });

  it('emits moveToTop with the chunk id on the Top button', async () => {
    const fixture = TestBed.createComponent(QueuePanelView);
    fixture.componentRef.setInput('entries', ENTRIES);
    let emitted: string | undefined;
    fixture.componentInstance.moveToTop.subscribe((id) => (emitted = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-chunk="ch_mid"] [data-testid="queue-move-top"]')?.click();
    expect(emitted).toBe('ch_mid');
  });

  it('emits group with the selected ids in current queue order on Group', async () => {
    const fixture = TestBed.createComponent(QueuePanelView);
    fixture.componentRef.setInput('entries', ENTRIES);
    let emitted: readonly string[] | undefined;
    fixture.componentInstance.group.subscribe((ids) => (emitted = ids));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const check = (chunkId: string): void => {
      el.querySelector<HTMLInputElement>(`[data-chunk="${chunkId}"] [data-testid="queue-select"]`)?.click();
    };
    check('ch_mid');
    check('ch_low');
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('[data-testid="group-selected"]')?.click();
    expect(emitted).toEqual(['ch_mid', 'ch_low']);
  });

  it('disables Group with fewer than two selected', async () => {
    const fixture = TestBed.createComponent(QueuePanelView);
    fixture.componentRef.setInput('entries', ENTRIES);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const button = el.querySelector<HTMLButtonElement>('[data-testid="group-selected"]');
    expect(button?.disabled).toBe(true);
  });

  it('shows the empty state for no entries', async () => {
    const fixture = TestBed.createComponent(QueuePanelView);
    fixture.componentRef.setInput('entries', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="queue-empty"]')).not.toBeNull();
  });
});
