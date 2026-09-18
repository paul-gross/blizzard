import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailHeader } from './chunk-detail-header';

/**
 * The chunk detail dock header's `⋯` overflow menu — its own panel contents
 * (Detach, Complete, Delete: rendering, gating, confirm-emit), split out of
 * `chunk-detail-header.spec.ts` because `KitMenuPanel` renders into a CDK
 * overlay attached to `document.body`, not the fixture's own DOM subtree
 * (`kit-menu.spec.ts`'s own convention) — a genuinely different harness shape
 * from the rest of that file, which never has to open anything. Pause/Resume
 * stay in the original file: they are not in the menu.
 */

const ISSUE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
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

/** The CDK renders every menu into an overlay attached to `document.body`, not
 * inside the fixture's own element — so panel assertions query the document
 * (`kit-menu.spec.ts`'s own convention). */
const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

async function openMenu(fixture: ReturnType<typeof TestBed.createComponent<ChunkDetailHeader>>, el: HTMLElement): Promise<void> {
  el.querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]')?.click();
  await fixture.whenStable();
}

describe('ChunkDetailHeader overflow menu', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkDetailHeader],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('withholds the actions menu trigger without chunk:control', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
  });

  // --- Detach (issue #42) ---------------------------------------------

  it('shows no Detach item for a chunk with no live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ISSUE_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="detach-chunk"]')).toBeNull();
  });

  it('shows a Detach item naming the runner for a chunk with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="detach-chunk"]')?.textContent).toContain('rn_01');
  });

  it('emits detach with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.detach.subscribe((chunkId) => (emitted = chunkId));
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('ch_01routed000000000000000000');
  });

  it('emits nothing when the operator declines the detach confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.detach.subscribe(() => (emitted = true));
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('disables Detach while the detach mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    fixture.componentRef.setInput('detachPending', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="detach-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    fixture.componentRef.setInput('detachPending', false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="detach-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('still shows a Detach item for a needs_human chunk that still carries a live route (not requeue)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ESCALATED_ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="detach-chunk"]')).not.toBeNull();
  });

  it('does not promise the ready queue in the confirm copy for a needs_human chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ESCALATED_ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();

    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).not.toContain('ready queue');
  });

  // --- Complete (issue #294) -------------------------------------------

  it('shows a Complete item, enabled, for a running chunk with chunk:control', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('keeps Complete enabled for a stopped chunk — unlike Stop, Complete has no un-complete verb', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'stopped' });
    fixture.componentRef.setInput('renderedStatus', 'stopped');
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('disables Complete for an already-done chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'done' });
    fixture.componentRef.setInput('renderedStatus', 'done');
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).toBe('true');
  });

  it('disables Complete while the complete mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    fixture.componentRef.setInput('completePending', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    fixture.componentRef.setInput('completePending', false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('emits complete with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.complete.subscribe((chunkId) => (emitted = chunkId));
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
  });

  it('emits nothing when the operator declines the complete confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.complete.subscribe(() => (emitted = true));
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('says the write cannot be undone in the complete confirm copy', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();

    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('Cannot be undone');
  });

  // --- Delete (D8, issue #364) -------------------------------------------

  it('enables Delete for an unacquired chunk (not_ready, ready) with chunk:control', async () => {
    for (const status of ['not_ready', 'ready'] as const) {
      const fixture = TestBed.createComponent(ChunkDetailHeader);
      fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status });
      fixture.componentRef.setInput('renderedStatus', status);
      fixture.componentRef.setInput('canControl', true);
      const el = fixture.nativeElement as HTMLElement;
      await fixture.whenStable();
      await openMenu(fixture, el);

      expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled'), status).not.toBe('true');
    }
  });

  it('disables Delete for an acquired or terminal status, even with chunk:control', async () => {
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
      fixture.componentRef.setInput('renderedStatus', status);
      fixture.componentRef.setInput('canControl', true);
      const el = fixture.nativeElement as HTMLElement;
      await fixture.whenStable();
      await openMenu(fixture, el);

      expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled'), status).toBe('true');
    }
  });

  it('disables Delete while the delete mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'ready' });
    fixture.componentRef.setInput('renderedStatus', 'ready');
    fixture.componentRef.setInput('canControl', true);
    fixture.componentRef.setInput('deletePending', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    fixture.componentRef.setInput('deletePending', false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('emits delete with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'ready' });
    fixture.componentRef.setInput('renderedStatus', 'ready');
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.delete.subscribe((chunkId) => (emitted = chunkId));
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(ISSUE_DETAIL.chunk_id);
  });

  it('emits nothing when the operator declines the delete confirm', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready' });
    fixture.componentRef.setInput('renderedStatus', 'not_ready');
    fixture.componentRef.setInput('canControl', true);
    let emitted = false;
    fixture.componentInstance.delete.subscribe(() => (emitted = true));
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('withdraws the hub items with no undo, in the delete confirm copy', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready' });
    fixture.componentRef.setInput('renderedStatus', 'not_ready');
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();

    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('withdraws its hub items');
    expect(message).toContain('Cannot be undone');
  });

  // --- Delete's dependents gate (D6) ------------------------------------

  it('disables Delete and names the dependents when another chunk still depends on it', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      status: 'ready',
      neighborhood: {
        prerequisites: [],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'not_ready', satisfied: false }],
      },
    });
    fixture.componentRef.setInput('renderedStatus', 'ready');
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    const item = inOverlay('[data-testid="delete-chunk"]');
    expect(item?.getAttribute('aria-disabled')).toBe('true');
    expect(item?.textContent).toContain('Blocked: C-0ccc depend on this');
  });

  it('falls back to the plain Delete subtitle once every dependent is satisfied', async () => {
    const fixture = TestBed.createComponent(ChunkDetailHeader);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      status: 'ready',
      neighborhood: {
        prerequisites: [],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'done', satisfied: true }],
      },
    });
    fixture.componentRef.setInput('renderedStatus', 'ready');
    fixture.componentRef.setInput('canControl', true);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture, el);

    const item = inOverlay('[data-testid="delete-chunk"]');
    expect(item?.getAttribute('aria-disabled')).not.toBe('true');
    expect(item?.textContent).toContain('Remove from the hub permanently');
  });
});
