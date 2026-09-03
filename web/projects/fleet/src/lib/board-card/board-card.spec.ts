import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

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
  blockedOn: null,
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

/** Render with an explicit `canControl`, returning the fixture itself (not just its
 * element) — a test that needs one of {@link BoardCardComponent}'s output subscriptions
 * (`delete`, `selectChunk`) needs the fixture, which the module-level `render` above
 * does not expose. */
async function renderWithControl(card: BoardCard, canControl: boolean | null) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  // `null`/pending resolves to `false` (hidden until confirmed) — the same convention
  // every other board control follows; a `null` input here stands in for "pending".
  if (canControl !== null) fixture.componentRef.setInput('canControl', canControl);
  await fixture.whenStable();
  return fixture;
}

describe('BoardCardComponent completion stamp (issue #173)', () => {
  it('renders the completion time on a done-lane card', async () => {
    const el = await render(BASE);

    const stamp = el.querySelector('[data-testid="chunk-done-at"]');
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
});

describe('BoardCardComponent DONE-column layout (issue #215)', () => {
  it('shows "done" once, in the upper-right node slot, and nothing in the lower-left status slot', async () => {
    const el = await render(BASE);

    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('done');
    expect(el.querySelector('[data-testid="chunk-status"]')).toBeNull();
  });

  it('shows "stopped" in the upper-right node slot for a stopped chunk, not its last node name', async () => {
    const el = await render({ ...BASE, status: 'stopped', node: 'deliver' });

    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('stopped');
    expect(el.querySelector('[data-testid="chunk-status"]')).toBeNull();
  });

  it('still names the status for a non-done-lane card', async () => {
    const el = await render({ ...BASE, status: 'running', node: 'build', completedAt: null });

    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('running');
    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('build');
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

describe('BoardCardComponent Delete (D8, issue #364)', () => {
  it('renders Delete for an unacquired card (not_ready, ready) with chunk:control', async () => {
    for (const status of ['not_ready', 'ready'] as const) {
      const fixture = await renderWithControl({ ...BASE, status }, true);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="delete-chunk"]'), status).not.toBeNull();
    }
  });

  it('renders no Delete for an acquired or terminal status, even with chunk:control', async () => {
    for (const status of [
      'running',
      'delivering',
      'waiting_on_human',
      'needs_human',
      'paused',
      'stopped',
      'done',
    ] as const) {
      const fixture = await renderWithControl({ ...BASE, status }, true);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="delete-chunk"]'), status).toBeNull();
    }
  });

  it('withholds Delete without chunk:control on an otherwise-eligible card', async () => {
    const fixture = await renderWithControl({ ...BASE, status: 'not_ready' }, false);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="delete-chunk"]')).toBeNull();
  });

  it('withholds Delete while chunk:control is still pending (null resolves to false)', async () => {
    const fixture = await renderWithControl({ ...BASE, status: 'ready' }, null);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="delete-chunk"]')).toBeNull();
  });

  it('emits nothing when the operator declines the delete confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = await renderWithControl({ ...BASE, status: 'not_ready' }, true);
    let emitted = false;
    fixture.componentInstance.delete.subscribe(() => (emitted = true));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="delete-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('emits delete with the card chunk id once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = await renderWithControl({ ...BASE, status: 'ready' }, true);
    let emitted: string | undefined;
    fixture.componentInstance.delete.subscribe((chunkId) => (emitted = chunkId));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="delete-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(BASE.chunkId);
    confirmSpy.mockRestore();
  });

  it('renders Promote and Delete side by side on a not_ready card, both controls reachable', async () => {
    const fixture = await renderWithControl({ ...BASE, status: 'not_ready' }, true);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="promote-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="delete-chunk"]')).not.toBeNull();
  });
});

describe('BoardCardComponent blocked marking (issue #461)', () => {
  it('renders nothing when the card carries no blockedOn', async () => {
    const el = await render({ ...BASE, status: 'ready', blockedOn: null });

    expect(el.querySelector('[data-testid="chunk-blocked"]')).toBeNull();
  });

  it('renders the marking naming the prerequisite when the card is blocked', async () => {
    const el = await render({ ...BASE, status: 'ready', blockedOn: 'ch_01prereq00000000000000000' });

    const marking = el.querySelector('[data-testid="chunk-blocked"]');
    expect(marking).not.toBeNull();
    expect(marking?.textContent).toContain('C-0000');
  });

  it('keeps every other control and the status unaffected by carrying a blockedOn', async () => {
    const el = await render({ ...BASE, status: 'running', node: 'build', blockedOn: 'ch_01prereq00000000000000000' });

    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('running');
    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('build');
  });

  it('emits selectChunk with the prerequisite id when the marking is clicked', async () => {
    const fixture = await renderWithControl(
      { ...BASE, status: 'ready', blockedOn: 'ch_01prereq00000000000000000' },
      null,
    );
    let emitted: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((chunkId) => (emitted = chunkId));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-blocked"]')?.click();

    expect(emitted).toBe('ch_01prereq00000000000000000');
  });
});
