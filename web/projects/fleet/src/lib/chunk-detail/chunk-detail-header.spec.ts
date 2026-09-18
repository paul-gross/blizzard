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

const ESCALATED_ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  status: 'needs_human',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  work_refs: [],
  history: [],
  artifacts: [],
  escalation: {
    epoch: 3,
    takeover_command: 'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
  },
  route: { runner_id: 'rn_02', workspace_id: 'ws_01', environment_ids: [] },
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

  // --- Detach (issue #42) ---------------------------------------------

  it('shows no Detach action for a chunk with no live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detach-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="route-info"]')).toBeNull();
  });

  it('shows the routed runner and a Detach action for a chunk with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="route-runner"]')?.textContent).toContain('rn_01');
    expect(el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')).not.toBeNull();
  });

  it('withholds Detach and Pause without chunk:control, even with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="route-runner"]')?.textContent).toContain('rn_01');
    expect(el.querySelector('[data-testid="detach-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
  });

  it('emits detach with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.detach.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('ch_01routed000000000000000000');
  });

  it('emits nothing when the operator declines the detach confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.detach.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('still shows a Detach action for a needs_human chunk that still carries a live route (not requeue)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detach-chunk"]')).not.toBeNull();
  });

  it('does not promise the ready queue in the confirm copy for a needs_human chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();

    await fixture.whenStable();
    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).not.toContain('ready queue');
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

  // --- Complete (issue #294) -------------------------------------------

  it('shows a Complete action for a running chunk with chunk:control', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')).not.toBeNull();
  });

  it('shows Complete for a stopped chunk — unlike Stop, Complete has no un-complete verb', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'stopped' });
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')).not.toBeNull();
  });

  it('shows no Complete action for an already-done chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'done' });
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="complete-chunk"]')).toBeNull();
  });

  it('withholds Complete without chunk:control', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="complete-chunk"]')).toBeNull();
  });

  it('emits complete with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.complete.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
  });

  it('emits nothing when the operator declines the complete confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.complete.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('warns there is no un-complete verb in the complete confirm copy', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();

    await fixture.whenStable();
    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('no un-complete verb');
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

  // --- Delete (D8, issue #364) -------------------------------------------

  it('shows Delete for an unacquired chunk (not_ready, ready) with chunk:control', async () => {
    for (const status of ['not_ready', 'ready'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status });
      fixture.componentRef.setInput('canControl', true);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="delete-chunk"]'), status).not.toBeNull();
    }
  });

  it('shows no Delete for an acquired or terminal status, even with chunk:control', async () => {
    for (const status of [
      'running',
      'delivering',
      'waiting_on_human',
      'needs_human',
      'paused',
      'stopped',
      'done',
    ] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status });
      fixture.componentRef.setInput('canControl', true);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="delete-chunk"]'), status).toBeNull();
    }
  });

  it('withholds Delete without chunk:control on an otherwise-eligible chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="delete-chunk"]')).toBeNull();
  });

  it('emits delete with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'ready' });
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.delete.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(ISSUE_DETAIL.chunk_id);
  });

  it('emits nothing when the operator declines the delete confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready' });
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.delete.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('withdraws the hub item(s) with no undo, in the delete confirm copy', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready' });
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="delete-chunk"]')?.click();

    await fixture.whenStable();
    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('withdraws its hub item(s)');
    expect(message).toContain('no undo');
  });
});
