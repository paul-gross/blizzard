import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

const BASE: BoardCard = {
  chunkId: 'ch_01done0000000000000000000',
  shortId: 'C-0000',
  status: 'done',
  node: 'done',
  nodeId: 'nd_done',
  pointerLabels: [],
  costUsd: 0,
  costPartial: false,
  completedAt: '2026-07-13T00:00:01+00:00',
};

async function render(card: BoardCard) {
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('BoardCardComponent completion stamp (issue #173)', () => {
  it('renders the completion time next to the status label on a done-lane card', async () => {
    const el = await render(BASE);

    const status = el.querySelector('[data-testid="chunk-status"]');
    const stamp = el.querySelector('[data-testid="chunk-done-at"]');
    expect(status?.textContent?.trim()).toBe('done');
    expect(stamp).not.toBeNull();
    // formatWhen's short form, via fleet-when — no new date formatter.
    expect(stamp?.textContent?.trim().length).toBeGreaterThan(0);
  });

  it('renders no timestamp for a done-lane card with no completedAt', async () => {
    const el = await render({ ...BASE, completedAt: null });

    expect(el.querySelector('[data-testid="chunk-done-at"]')).toBeNull();
  });

  it('renders no timestamp outside the done lane', async () => {
    const el = await render({ ...BASE, status: 'running', completedAt: null });

    expect(el.querySelector('[data-testid="chunk-done-at"]')).toBeNull();
  });

  it('renders no timestamp for a non-done-lane card even if it somehow carries a completedAt', async () => {
    // Defensive: the lane gates the render, not completedAt's own null-ness alone.
    const el = await render({ ...BASE, status: 'running', completedAt: '2026-07-13T00:00:01+00:00' });

    expect(el.querySelector('[data-testid="chunk-done-at"]')).toBeNull();
  });

  it("keeps chunk-status's text exactly the status string", async () => {
    const el = await render(BASE);

    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('done');
  });
});

describe('BoardCardComponent work-ref chips (issue #176)', () => {
  it('renders one chip per pointer label, in order, each carrying its own title', async () => {
    const el = await render({
      ...BASE,
      pointerLabels: ['blizzard#146', 'blizzard#164', 'widget#9'],
    });

    const chips = el.querySelectorAll('[data-testid="work-ref-chip"]');
    expect(Array.from(chips).map((c) => c.textContent?.trim())).toEqual([
      'blizzard#146',
      'blizzard#164',
      'widget#9',
    ]);
    expect(Array.from(chips).map((c) => c.getAttribute('title'))).toEqual([
      'blizzard#146',
      'blizzard#164',
      'widget#9',
    ]);
  });

  it('renders exactly one chip for a single pointer label', async () => {
    const el = await render({ ...BASE, pointerLabels: ['blizzard#146'] });

    const chips = el.querySelectorAll('[data-testid="work-ref-chip"]');
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent?.trim()).toBe('blizzard#146');
  });

  it('renders no chip when there are no pointer labels', async () => {
    const el = await render({ ...BASE, pointerLabels: [] });

    expect(el.querySelectorAll('[data-testid="work-ref-chip"]')).toHaveLength(0);
  });
});
