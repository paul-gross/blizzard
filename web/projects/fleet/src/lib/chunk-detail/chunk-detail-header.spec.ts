import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';

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

/** A chunk in a status the hub's dependency service actually admits a declare
 * against (`PRE_CLAIM_STATUSES`) — `ISSUE_DETAIL`/`ROUTED_DETAIL` are deliberately
 * `running` so Declare's own gating (round 3 F3) has a fixture to prove itself off. */
const DECLARABLE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01declarable000000000000000',
  graph_id: 'gr_1',
  status: 'ready',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
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
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.detach.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe('ch_01routed000000000000000000');
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator declines the detach confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.detach.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
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
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    const message = confirmSpy.mock.calls[0][0];
    expect(message).not.toContain('ready queue');
    confirmSpy.mockRestore();
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
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-status"]')?.textContent).toContain('waiting_on_human');
    expect(el.querySelector('[data-testid="chunk-pause-by"]')?.textContent).toContain('operator');
    expect(el.querySelector('[data-testid="resume-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.click();
    expect(resumed).toBe(ROUTED_DETAIL.chunk_id);
    confirmSpy.mockRestore();
  });

  it('emits pauseChunk with the chunk id once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.pauseChunk.subscribe((id) => (emitted = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator declines the pause confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.pauseChunk.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator declines the resume confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.resumeChunk.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="resume-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('does not claim the claim is given up in the pause confirm copy — that is detach', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();

    const message = confirmSpy.mock.calls[0][0];
    expect(message).toContain('keeps the');
    expect(message).toContain('claim');
    confirmSpy.mockRestore();
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
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.complete.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator declines the complete confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.complete.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('warns there is no un-complete verb in the complete confirm copy', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();

    const message = confirmSpy.mock.calls[0][0];
    expect(message).toContain('no un-complete verb');
    confirmSpy.mockRestore();
  });

  it('renders no blocked marking for a chunk carrying none', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-blocked"]')).toBeNull();
  });

  it('renders the blocked marking naming the prerequisite beside the status', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01prereq00000000000000000' },
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const marking = el.querySelector('[data-testid="chunk-blocked"]');
    expect(marking).not.toBeNull();
    expect(el.querySelector('[data-testid="detail-status"]')?.textContent?.trim()).toBe('running');
  });

  it('emits selectChunk with the prerequisite id when the marking is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01prereq00000000000000000' },
    });
    let emitted: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-blocked"]')?.click();

    expect(emitted).toBe('ch_01prereq00000000000000000');
  });

  it('offers declare/release with chunk:control, withholds both without it', async () => {
    const withControl = TestBed.createComponent(ChunkDetailHeader);
    withControl.componentRef.setInput('detail', DECLARABLE_DETAIL);
    withControl.componentRef.setInput('canControl', true);
    await withControl.whenStable();
    const withEl = withControl.nativeElement as HTMLElement;
    expect(withEl.querySelector('[data-testid="declare-dependency"]')).not.toBeNull();
    expect(withEl.querySelector('[data-testid="release-dependency"]')).not.toBeNull();

    const withoutControl = TestBed.createComponent(ChunkDetailHeader);
    withoutControl.componentRef.setInput('detail', DECLARABLE_DETAIL);
    withoutControl.componentRef.setInput('canControl', false);
    await withoutControl.whenStable();
    const withoutEl = withoutControl.nativeElement as HTMLElement;
    expect(withoutEl.querySelector('[data-testid="declare-dependency"]')).toBeNull();
    expect(withoutEl.querySelector('[data-testid="release-dependency"]')).toBeNull();
  });

  it('withholds Declare for a status the hub refuses (round 3 F3), but still offers Release', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL); // status: 'running', outside PRE_CLAIM_STATUSES
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="declare-dependency"]')).toBeNull();
    expect(el.querySelector('[data-testid="release-dependency"]')).not.toBeNull();
  });

  it('offers Declare for both statuses the hub actually admits it against', async () => {
    for (const status of ['not_ready', 'ready'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...DECLARABLE_DETAIL, status });
      fixture.componentRef.setInput('canControl', true);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="declare-dependency"]'), status).not.toBeNull();
    }
  });

  it('prefills the prerequisite field from the marking when one stands, empty otherwise', async () => {
    const blocked = TestBed.createComponent(ChunkDetailHeader);
    blocked.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01prereq00000000000000000' },
    });
    blocked.componentRef.setInput('canControl', true);
    await blocked.whenStable();
    const blockedEl = blocked.nativeElement as HTMLElement;
    const blockedInput = blockedEl.querySelector<HTMLInputElement>('[data-testid="dependency-prerequisite-input"]');
    expect(blockedInput?.value).toBe('ch_01prereq00000000000000000');

    const plain = TestBed.createComponent(ChunkDetailHeader);
    plain.componentRef.setInput('detail', ISSUE_DETAIL);
    plain.componentRef.setInput('canControl', true);
    await plain.whenStable();
    const plainEl = plain.nativeElement as HTMLElement;
    const plainInput = plainEl.querySelector<HTMLInputElement>('[data-testid="dependency-prerequisite-input"]');
    expect(plainInput?.value).toBe('');
  });

  it('emits declareDependency with the field value once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', DECLARABLE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: { chunkId: string; prerequisiteChunkId: string } | undefined;
    fixture.componentInstance.declareDependency.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector<HTMLInputElement>('[data-testid="dependency-prerequisite-input"]')!;
    input.value = 'ch_01prereq00000000000000000';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();

    expect(emitted).toEqual({
      chunkId: DECLARABLE_DETAIL.chunk_id,
      prerequisiteChunkId: 'ch_01prereq00000000000000000',
    });
    confirmSpy.mockRestore();
  });

  it('emits nothing when the operator declines the declare confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', DECLARABLE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.declareDependency.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector<HTMLInputElement>('[data-testid="dependency-prerequisite-input"]')!;
    input.value = 'ch_01prereq00000000000000000';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('emits releaseDependency with the field value once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01prereq00000000000000000' },
    });
    fixture.componentRef.setInput('canControl', true);
    let emitted: { chunkId: string; prerequisiteChunkId: string } | undefined;
    fixture.componentInstance.releaseDependency.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="release-dependency"]')?.click();

    expect(emitted).toEqual({ chunkId: ISSUE_DETAIL.chunk_id, prerequisiteChunkId: 'ch_01prereq00000000000000000' });
    confirmSpy.mockRestore();
  });

  it('emits nothing when Declare is clicked with a blank field, without prompting to confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', DECLARABLE_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.declareDependency.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="declare-dependency"]')?.click();

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(emitted).toBe(false);
    confirmSpy.mockRestore();
  });

  it('keeps an in-progress edit through a same-chunk marking change, but resets on an actual chunk switch (round 3 F1)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...DECLARABLE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01prereq00000000000000000' },
    });
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector<HTMLInputElement>('[data-testid="dependency-prerequisite-input"]')!;
    expect(input.value).toBe('ch_01prereq00000000000000000');

    // The operator starts typing a different prerequisite.
    input.value = 'ch_01unsaved0000000000000000';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    // The marking changes on the *same* chunk — a poll or SSE refresh, or another
    // operator declaring/releasing — must not wipe the in-progress edit.
    fixture.componentRef.setInput('detail', {
      ...DECLARABLE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01different000000000000000' },
    });
    await fixture.whenStable();
    expect(input.value).toBe('ch_01unsaved0000000000000000');

    // An actual chunk switch still re-prefills from the new chunk's own marking.
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      blocked: { prerequisite_chunk_id: 'ch_01other00000000000000000' },
    });
    await fixture.whenStable();
    expect(input.value).toBe('ch_01other00000000000000000');
  });
});
