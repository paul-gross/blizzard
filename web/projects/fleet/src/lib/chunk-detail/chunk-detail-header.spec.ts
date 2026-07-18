import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailHeader } from './chunk-detail-header';

const ISSUE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' },
  ],
  history: [],
  artifacts: [],
};

const ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

const ESCALATED_ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'needs_human',
  current_node_id: 'nd_build',
  latest_epoch: 3,
  pm_pointers: [],
  history: [],
  artifacts: [],
  escalation: { epoch: 3, takeover_command: 'blizzard runner takeover ch_01esc00000000000000000000000' },
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
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('names the chunk and its work item the way the board card does', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-id"]')?.textContent?.trim()).toBe(ISSUE_DETAIL.chunk_id);
    const pointer = el.querySelector<HTMLAnchorElement>('a[data-testid="detail-pointer"]');
    expect(pointer?.textContent?.trim()).toBe('widget#42');
    expect(pointer?.getAttribute('href')).toBe('https://github.com/acme/widget/issues/42');
    expect(el.querySelector('[data-testid="detail-status"]')?.textContent).toContain('running');
  });

  it('surfaces who paused a chunk in the header (issue #46)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-pause-by"]')?.textContent).toContain('operator');
  });

  it('shows no chunk-pause-by when the chunk carries no open pause fact', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-pause-by"]')).toBeNull();
  });

  it('emits dismiss when the close button is activated', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
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
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detach-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="route-info"]')).toBeNull();
  });

  it('shows the routed runner and a Detach action for a chunk with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="route-runner"]')?.textContent).toContain('rn_01');
    expect(el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')).not.toBeNull();
  });

  it('emits detach with the chunk id once the operator confirms', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
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
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detach-chunk"]')).not.toBeNull();
  });

  it('does not promise the ready queue in the confirm copy for a needs_human chunk', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
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
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="pause-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="resume-chunk"]')).toBeNull();
  });

  it('shows no Pause for a chunk the hub would refuse to pause (done/stopped/delivering)', async () => {
    for (const status of ['done', 'stopped', 'delivering'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status });
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="pause-chunk"]'), status).toBeNull();
    }
  });

  it('still offers Pause for a waiting_on_human / needs_human chunk — the lever stays broad', async () => {
    for (const status of ['waiting_on_human', 'needs_human'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status });
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="pause-chunk"]'), status).not.toBeNull();
    }
  });

  it('shows Resume — not Pause — for a paused chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('paused'));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="resume-chunk"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
  });

  it('offers Resume — not Pause — for a paused chunk whose status reads waiting_on_human (issue #46)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', pausedDetail('waiting_on_human'));
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
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="pause-chunk"]')?.click();

    const message = confirmSpy.mock.calls[0][0];
    expect(message).toContain('keeps the');
    expect(message).toContain('claim');
    confirmSpy.mockRestore();
  });
});
