import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

const BASE: BoardCard = {
  chunkId: 'ch_01done0000000000000000000',
  shortId: 'C-0000',
  status: 'done',
  terminal: true,
  promotable: false,
  node: 'done',
  nodeId: 'nd_done',
  pointerLabels: [],
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: '2026-07-13T00:00:01+00:00',
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

async function renderFixture(card: BoardCard, gated = false, canControl = false) {
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  fixture.componentRef.setInput('gated', gated);
  fixture.componentRef.setInput('canControl', canControl);
  await fixture.whenStable();
  return fixture;
}

async function render(card: BoardCard, gated = false, canControl = false) {
  return (await renderFixture(card, gated, canControl)).nativeElement as HTMLElement;
}

/** `n` titles, "Item 1" … "Item n". */
const items = (n: number) => Array.from({ length: n }, (_, i) => `Item ${i + 1}`);

const titlesOf = (el: HTMLElement) =>
  [...el.querySelectorAll('[data-testid="chunk-title"]')].map((title) => title.textContent?.trim());

describe('BoardCardComponent work item titles', () => {
  const RUNNING: BoardCard = { ...BASE, status: 'running', terminal: false, node: 'build', completedAt: null };

  it('leads with the one work item title in place of the id and work-ref chips', async () => {
    const el = await render({ ...RUNNING, pointerLabels: ['hub#7'], titles: ['Migrate the event log'] });
    expect(titlesOf(el)).toEqual(['Migrate the event log']);
    expect(el.querySelector('[data-testid="work-ref-chip"]')).toBeNull();
  });

  it('leads with the work-ref labels while the titles are unread', async () => {
    const el = await render({ ...RUNNING, pointerLabels: ['hub#7'], titles: null });
    expect(titlesOf(el)).toEqual([]);
    expect(el.querySelector('[data-testid="work-ref-chip"]')?.textContent?.trim()).toBe('hub#7');
  });

  it('keeps the same step row titled or not — the step, then the id', async () => {
    for (const titles of [null, items(1)]) {
      const el = await render({ ...RUNNING, shortId: 'C-QQFM', titles });
      const row = el.querySelector('[data-testid="chunk-node"]')!.parentElement!;
      expect(row.firstElementChild?.getAttribute('data-testid')).toBe('chunk-node');
      expect(row.lastElementChild?.textContent?.trim()).toBe('C-QQFM');
      TestBed.resetTestingModule();
    }
  });

  it('names every item of a group of three, with no fold', async () => {
    const el = await render({ ...RUNNING, titles: items(3) });
    expect(titlesOf(el)).toEqual(['Item 1', 'Item 2', 'Item 3']);
    expect(el.querySelector('[data-testid="chunk-title-fold"]')).toBeNull();
  });

  it('folds a larger group to its first two titles, and opens in place without selecting', async () => {
    const fixture = await renderFixture({ ...RUNNING, titles: items(5) });
    const el = fixture.nativeElement as HTMLElement;
    const selected = vi.fn();
    fixture.componentInstance.selectChunk.subscribe(selected);
    expect(titlesOf(el)).toEqual(['Item 1', 'Item 2']);

    const fold = el.querySelector<HTMLElement>('[data-testid="chunk-title-fold"]')!;
    expect(fold.textContent?.trim()).toBe('+3 more');
    fold.click();
    await fixture.whenStable();

    expect(titlesOf(el)).toEqual(['Item 1', 'Item 2', 'Item 3', 'Item 4', 'Item 5']);
    expect(el.querySelector('[data-testid="chunk-title-fold"]')?.textContent?.trim()).toBe('show less');
    expect(selected).not.toHaveBeenCalled();
  });

  it("drops the status word where the lane's heading already names it, titled or not", async () => {
    for (const titles of [null, items(1)]) {
      const el = await render({ ...RUNNING, titles });
      expect(el.querySelector('[data-testid="chunk-status"]')).toBeNull();
      TestBed.resetTestingModule();
    }
  });

  it('keeps the status word where it says more than the lane', async () => {
    const el = await render({ ...RUNNING, status: 'paused', titles: items(1) });
    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('paused');
  });
});


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
    const el = await render({ ...BASE, status: 'running', terminal: false, completedAt: null });

    expect(el.querySelector('[data-testid="chunk-done-at"]')).toBeNull();
  });

  it('renders no timestamp for a non-done-lane card even if it somehow carries a completedAt', async () => {
    // Defensive: the lane gates the render, not completedAt's own null-ness alone.
    const el = await render({ ...BASE, status: 'running', terminal: false, completedAt: '2026-07-13T00:00:01+00:00' });

    expect(el.querySelector('[data-testid="chunk-done-at"]')).toBeNull();
  });
});

describe('BoardCardComponent DONE-column layout (issue #215)', () => {
  it('shows "done" once, in the step slot, and no status word', async () => {
    const el = await render(BASE);

    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('done');
    expect(el.querySelector('[data-testid="chunk-status"]')).toBeNull();
  });

  it('shows "stopped" in the step slot for a stopped chunk, not its last node name', async () => {
    const el = await render({ ...BASE, status: 'stopped', node: 'deliver' });

    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('stopped');
    expect(el.querySelector('[data-testid="chunk-status"]')).toBeNull();
  });

  it('still names the status for a non-done-lane card', async () => {
    const el = await render({ ...BASE, status: 'delivering', terminal: false, node: 'build', completedAt: null });

    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('delivering');
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

describe('BoardCardComponent cost figure', () => {
  it('renders an entirely estimated card as the estimate alone, marked ~', async () => {
    const el = await render({ ...BASE, costUsd: 0, costPartial: false, estimatedCostUsd: 0.07 });

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent?.trim()).toBe('~$0.07');
  });

  it('renders no cost figure for a card with no billed cost, no estimate, and not partial', async () => {
    const el = await render(BASE);

    expect(el.querySelector('[data-testid="card-cost"]')).toBeNull();
  });

  it('folds a billed cost and its own estimate into one figure, marked ~', async () => {
    const el = await render({ ...BASE, costUsd: 0.42, costPartial: false, estimatedCostUsd: 0.07 });

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent?.trim()).toBe('~$0.49');
  });

  it('marks a partial billed cost with a trailing +', async () => {
    const el = await render({ ...BASE, costUsd: 0.42, costPartial: true, estimatedCostUsd: null });

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent?.trim()).toBe('$0.42+');
  });
});

describe('BoardCardComponent blocked marking (issue #461)', () => {
  it('renders nothing when the card carries no blockedOn', async () => {
    const el = await render({ ...BASE, status: 'ready', terminal: false, blockedOn: null, blockedCount: 0 });

    expect(el.querySelector('[data-testid="chunk-blocked-by"]')).toBeNull();
  });

  it('names the one prerequisite by its compact ref when exactly one is unmet', async () => {
    const el = await render({ ...BASE, status: 'ready', terminal: false, blockedOn: 'ch_01prereq00000000000000000', blockedCount: 1 });

    const marking = el.querySelector('[data-testid="chunk-blocked-by"]');
    expect(marking?.textContent?.replace(/\s+/g, ' ').trim()).toBe('blocked by C-0000');
  });

  it("qualifies a single blocker with that blocker's own state", async () => {
    const el = await render({
      ...BASE,
      status: 'ready',
      terminal: false,
      blockedOn: 'ch_01prereq00000000000000000',
      blockedCount: 1,
      blockedOnStatus: 'running',
    });

    const marking = el.querySelector('[data-testid="chunk-blocked-by"]');
    expect(marking?.textContent?.replace(/\s+/g, ' ').trim()).toBe('blocked by C-0000 (running)');
  });

  it('withholds the state when several chunks block it — a count names no single state', async () => {
    const el = await render({
      ...BASE,
      status: 'ready',
      terminal: false,
      blockedOn: 'ch_01prereq00000000000000000',
      blockedCount: 2,
      blockedOnStatus: 'running',
    });

    expect(el.querySelector('[data-testid="chunk-blocked-by-state"]')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-blocked-by"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
      'blocked by 2 chunks',
    );
  });

  it('counts the prerequisites instead of naming the first when more than one is unmet', async () => {
    const el = await render({ ...BASE, status: 'ready', terminal: false, blockedOn: 'ch_01prereq00000000000000000', blockedCount: 2 });

    const marking = el.querySelector('[data-testid="chunk-blocked-by"]');
    expect(marking?.textContent?.replace(/\s+/g, ' ').trim()).toBe('blocked by 2 chunks');
    expect(marking?.textContent).not.toContain('C-0000');
  });

  it('references the prerequisite rather than reaching it — no link, no nested control', async () => {
    const el = await render({ ...BASE, status: 'ready', terminal: false, blockedOn: 'ch_01prereq00000000000000000', blockedCount: 1 });

    const marking = el.querySelector('[data-testid="chunk-blocked-by"]')!;
    expect(marking.querySelector('a, button')).toBeNull();
    expect(marking.tagName).toBe('SPAN');
  });

  it('keeps every other control and the status unaffected by carrying a blockedOn', async () => {
    const el = await render({
      ...BASE,
      status: 'paused',
      terminal: false,
      node: 'build',
      blockedOn: 'ch_01prereq00000000000000000',
      blockedCount: 1,
    });

    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('paused');
    expect(el.querySelector('[data-testid="chunk-node"]')?.textContent?.trim()).toBe('build');
  });
});

describe('BoardCardComponent delivery block', () => {
  const PR = { repo: 'widget', number: 42, url: 'https://forge.example/widget/pull/42' };
  const LANDED = { repo: 'service', commit_hash: 'abc123', url: 'https://forge.example/service/commit/abc123' };

  it('shows only the open PR link on a delivering chunk, never a landed hash', async () => {
    const el = await render({ ...BASE, status: 'delivering', terminal: false, node: 'deliver', openPrs: [PR], landedRepos: [LANDED] });

    expect(el.querySelector('[data-testid="card-pr-link"]')?.textContent).toContain('widget PR #42');
    expect(el.querySelector('[data-testid="card-landed-link"]')).toBeNull();
    expect(el.querySelector('[data-testid="card-landed-text"]')).toBeNull();
  });

  it('shows no delivery block on a landed done-lane chunk', async () => {
    const el = await render({ ...BASE, status: 'done', landedRepos: [LANDED] });

    expect(el.querySelector('[data-testid="card-delivery"]')).toBeNull();
  });

  it('shows no delivery block on a stopped chunk whose PR is still open', async () => {
    const el = await render({ ...BASE, status: 'stopped', openPrs: [PR] });

    expect(el.querySelector('[data-testid="card-delivery"]')).toBeNull();
  });

  it('shows the open PR link in the needs-human lane', async () => {
    const needs = await render({ ...BASE, status: 'needs_human', terminal: false, openPrs: [PR] });
    expect(needs.querySelector('[data-testid="card-pr-link"]')).not.toBeNull();
  });
});

describe('BoardCardComponent gate marker', () => {
  const WAITING: BoardCard = { ...BASE, status: 'waiting_on_human', terminal: false, node: 'approve-gate', completedAt: null };

  it('marks a card `gate` when its chunk has an open decision, beside the unchanged status', async () => {
    const el = await render(WAITING, true);

    expect(el.querySelector('[data-testid="chunk-gate"]')?.textContent?.trim()).toBe('gate');
    expect(el.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('waiting_on_human');
  });

  it('carries no marker when the chunk is not flagged', async () => {
    const el = await render(WAITING);

    expect(el.querySelector('[data-testid="chunk-gate"]')).toBeNull();
  });
});

describe('BoardCardComponent open control', () => {
  const PR = { repo: 'widget', number: 42, url: 'https://forge.example/widget/pull/42' };
  const DELIVERING: BoardCard = {
    ...BASE,
    status: 'delivering',
    terminal: false,
    promotable: true,
    completedAt: null,
    openPrs: [PR],
    titles: items(5),
  };

  it("names the focusable button 'Open chunk <id>'", async () => {
    const el = await render({ ...BASE, shortId: 'C-OPEN' });

    const button = el.querySelector('fleet-kit-select-row button');
    expect(button?.getAttribute('aria-label')).toBe('Open chunk C-OPEN');
  });

  it('opens the chunk from its button and from a click on the rows over it', async () => {
    const fixture = await renderFixture(DELIVERING, false, true);
    const el = fixture.nativeElement as HTMLElement;
    const selected = vi.fn();
    fixture.componentInstance.selectChunk.subscribe(selected);

    el.querySelector<HTMLElement>('fleet-kit-select-row button')!.click();
    el.querySelector<HTMLElement>('[data-testid="chunk-title"]')!.click();
    el.querySelector<HTMLElement>('[data-testid="chunk-id"]')!.click();

    expect(selected.mock.calls).toEqual([[DELIVERING.chunkId], [DELIVERING.chunkId], [DELIVERING.chunkId]]);
  });

  it('leaves the PR link and Promote to act for themselves', async () => {
    const fixture = await renderFixture(DELIVERING, false, true);
    const el = fixture.nativeElement as HTMLElement;
    const selected = vi.fn();
    const promoted = vi.fn();
    fixture.componentInstance.selectChunk.subscribe(selected);
    fixture.componentInstance.promote.subscribe(promoted);

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="card-pr-link"]')!;
    link.addEventListener('click', (event) => event.preventDefault());
    link.click();
    el.querySelector<HTMLElement>('[data-testid="promote-chunk"]')!.click();

    expect(selected).not.toHaveBeenCalled();
    expect(promoted).toHaveBeenCalledWith(DELIVERING.chunkId);
  });
});

describe('BoardCardComponent Promote gate', () => {
  it('offers Promote on a paused, never-promoted card the hub flags promotable', async () => {
    const el = await render({ ...BASE, status: 'paused', terminal: false, promotable: true, completedAt: null }, false, true);

    expect(el.querySelector('[data-testid="promote-chunk"]')).not.toBeNull();
  });

  it('withholds Promote on a not_ready card the hub does not flag promotable', async () => {
    const el = await render(
      { ...BASE, status: 'not_ready', terminal: false, promotable: false, completedAt: null },
      false,
      true,
    );

    expect(el.querySelector('[data-testid="promote-chunk"]')).toBeNull();
  });

  it('withholds Promote from an identity without chunk control even when promotable', async () => {
    const el = await render({ ...BASE, status: 'not_ready', terminal: false, promotable: true, completedAt: null });

    expect(el.querySelector('[data-testid="promote-chunk"]')).toBeNull();
  });
});

describe('BoardCardComponent done-lane membership', () => {
  it("renders the completion stamp off the hub's terminal flag, not the status value", async () => {
    const flagged = await render({ ...BASE, status: 'running', terminal: true });

    expect(flagged.querySelector('[data-testid="chunk-done-at"]')).not.toBeNull();
    expect(flagged.querySelector('[data-testid="chunk-status"]')).toBeNull();
  });

  it('treats an unflagged done-status card as outside the done lane', async () => {
    const unflagged = await render({ ...BASE, status: 'done', terminal: false });

    expect(unflagged.querySelector('[data-testid="chunk-done-at"]')).toBeNull();
    expect(unflagged.querySelector('[data-testid="chunk-status"]')?.textContent?.trim()).toBe('done');
  });
});
