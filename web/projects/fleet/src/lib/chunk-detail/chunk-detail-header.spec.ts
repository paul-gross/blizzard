import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailHeader } from './chunk-detail-header';

const ISSUE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' },
  ],
  history: [],
  artifacts: [],
};

const ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

/** A chunk carrying an open pause fact, whatever its derived status reads. */
function pausedDetail(status: ChunkDetail['status'], extra: Partial<ChunkDetail> = {}): ChunkDetail {
  return {
    ...ROUTED_DETAIL,
    status,
    pause: { by: 'operator', set_at: '2026-07-16T00:00:00Z' },
    ...extra,
  };
}

describe('ChunkDetailHeader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkDetailHeader],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('names the chunk and its work item the way the board card does', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const idLink = el.querySelector<HTMLAnchorElement>('[data-testid="detail-id"]');
    expect(idLink?.textContent?.trim()).toBe(ISSUE_DETAIL.chunk_id);
    // At narrow widths the id truncates with an ellipsis (styles), so the full id
    // stays recoverable through the title attribute rather than the rendered text (issue #138).
    expect(idLink?.getAttribute('title')).toBe(ISSUE_DETAIL.chunk_id);
    // The chunk longname links out to its dedicated page (issue #205).
    expect(idLink?.getAttribute('href')).toBe(`/board/chunk/${ISSUE_DETAIL.chunk_id}`);
    const pointer = el.querySelector<HTMLAnchorElement>('a[data-testid="detail-pointer"]');
    expect(pointer?.textContent?.trim()).toBe('widget#42');
    expect(pointer?.getAttribute('href')).toBe('https://github.com/acme/widget/issues/42');
    expect(el.querySelector('[data-testid="detail-status"]')?.textContent).toContain('running');
  });

  it('surfaces who paused a chunk in the header (issue #46)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-pause-by"]')?.textContent).toContain('operator');
  });

  it('shows no chunk-pause-by when the chunk carries no open pause fact', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-pause-by"]')).toBeNull();
  });

  it('emits dismiss when the close button is activated', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let closed = false;
    fixture.componentInstance.dismiss.subscribe(() => (closed = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detail-close"]')?.click();
    expect(closed).toBe(true);
  });

  // --- The "Claimed by" chip (issue #42) --------------------------------

  it('shows no claimed-by chip for a chunk with no live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="route-info"]')).toBeNull();
  });

  it('shows the routed runner in a plain "Claimed by" chip, no "Route" label', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const chip = el.querySelector('[data-testid="route-info"]');
    expect(chip?.textContent?.trim()).toBe('Claimed by rn_01');
  });

  it('withholds Pause without chunk:control, even with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="route-info"]')?.textContent).toContain('rn_01');
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
  });

  // --- Pause / Resume (issue #46) -------------------------------------------

  it('shows Pause — not Resume — for a running chunk carrying no pause fact', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="pause-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="resume-chunk"]')).toBeNull();
  });

  it('shows no Pause for a chunk the hub would refuse to pause (done/stopped/delivering)', async () => {
    for (const status of ['done', 'stopped', 'delivering'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status });
    fixture.componentRef.setInput('canControl', true);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="pause-chunk"]'), status).toBeNull();
    }
  });

  it('still offers Pause for a waiting_on_human / needs_human chunk — the lever stays broad', async () => {
    for (const status of ['waiting_on_human', 'needs_human'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status });
    fixture.componentRef.setInput('canControl', true);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="pause-chunk"]'), status).not.toBeNull();
    }
  });

  it('shows Resume — not Pause — for a paused chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="resume-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
  });

  it('offers Resume — not Pause — for a paused chunk whose status reads waiting_on_human (issue #46)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('waiting_on_human'));
    fixture.componentRef.setInput('canControl', true);
    let resumed: string | undefined;
    fixture.componentInstance.resumeChunk.subscribe((id) => (resumed = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-status"]')?.textContent).toContain('waiting_on_human');
    expect(el.querySelector('[data-testid="chunk-pause-by"]')?.textContent).toContain('operator');
    expect(el.querySelector('[data-testid="resume-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    expect(resumed).toBe(ROUTED_DETAIL.chunk_id);
  });

  it('emits pauseChunk with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.pauseChunk.subscribe((id) => (emitted = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
  });

  it('emits nothing when the operator declines the pause confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.pauseChunk.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('disables Pause while the pause mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    fixture.componentRef.setInput('pausePending', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('pausePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.disabled).toBe(false);
  });

  it('disables Resume while the pause mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    fixture.componentRef.setInput('canControl', true);
    fixture.componentRef.setInput('pausePending', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.disabled).toBe(true);

    fixture.componentRef.setInput('pausePending', false);
    await fixture.whenStable();

    expect(el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.disabled).toBe(false);
  });

  it('emits nothing when the operator declines the resume confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.resumeChunk.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('does not claim the claim is given up in the pause confirm copy — that is detach', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();

    await fixture.whenStable();
    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('keeps the');
    expect(message).toContain('claim');
  });

  it('names no edge on the identity line for a chunk carrying none', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-blocked-by"]')).toBeNull();
    expect(el.querySelector('[data-testid="detail-blocking"]')).toBeNull();
  });

  it('names every unmet prerequisite and every chunk it still blocks, beside the status', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      neighborhood: {
        prerequisites: [
          { chunk_id: 'ch_01prereq00000000000000aaa', status: 'ready', satisfied: false },
          { chunk_id: 'ch_01prereq00000000000000bbb', status: 'running', satisfied: false },
        ],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'not_ready', satisfied: false }],
      },
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const blockedBy = [...el.querySelectorAll('[data-testid="detail-blocked-by"]')].map((n) =>
      n.textContent?.replace(/\s+/g, ' ').trim(),
    );
    const blocking = [...el.querySelectorAll('[data-testid="detail-blocking"]')].map((n) =>
      n.textContent?.replace(/\s+/g, ' ').trim(),
    );
    expect(blockedBy).toEqual(['blocked by C-0aaa', 'blocked by C-0bbb']);
    expect(blocking).toEqual(['blocking C-0ccc']);
    expect(el.querySelector('[data-testid="detail-status"]')?.textContent?.trim()).toBe('running');
  });

  it('leaves a satisfied edge off the line — it blocks nothing', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      neighborhood: {
        prerequisites: [{ chunk_id: 'ch_01prereq00000000000000aaa', status: 'done', satisfied: true }],
        dependents: [],
      },
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-blocked-by"]')).toBeNull();
  });

  it('separates every item on the identity line with a dot', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      neighborhood: {
        prerequisites: [{ chunk_id: 'ch_01prereq00000000000000aaa', status: 'ready', satisfied: false }],
        dependents: [],
      },
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // One pointer, the status, one edge — two gaps between the three, so two dots.
    expect(el.querySelectorAll('[data-testid="detail-pointer"]')).toHaveLength(1);
    expect(el.querySelectorAll('.d-sub .sep')).toHaveLength(2);
  });
});
