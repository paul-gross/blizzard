import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../../api/hub';
import { runnerDisplayName } from '../../core/runner-display-name';
import { chunkControlStub, chunkDetail, chunkEscalationView, type ChunkControlStub } from 'fleet/testing';
import { ChunkActionsMenu } from './chunk-actions-menu';

/**
 * A chunk header's ⋮ actions menu — Pause/Resume, Detach, Complete, Delete: rendering,
 * gating, and confirm-then-fire through the {@link ChunkControlStub} port. The menu panel
 * renders under `document.body`, not the fixture's own DOM subtree, so these tests query there.
 */

const ISSUE_DETAIL: ChunkDetail = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
});

const ROUTED_DETAIL: ChunkDetail = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', runner_name: 'r-claude', workspace_id: 'ws_01', environment_ids: ['env_01'] },
});

const ESCALATED_ROUTED_DETAIL: ChunkDetail = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_01esc00000000000000000000000',
  graph_id: 'gr_1',
  status: 'needs_human',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 3,
  work_refs: [],
  history: [],
  artifacts: [],
  escalation: chunkEscalationView({
    epoch: 3,
    takeover_command: 'cd /work/ch_01esc00000000000000000000000 && claude --resume se_01',
    cause: null,
    detail: null,
    wrapped_takeover_command: '',
  }),
  route: { runner_id: 'rn_02', runner_name: null, workspace_id: 'ws_01', environment_ids: [] },
});

/** A chunk carrying an open pause fact, whatever its derived status reads. */
function pausedDetail(status: ChunkDetail['status'], extra: Partial<ChunkDetail> = {}): ChunkDetail {
  return {
    ...ROUTED_DETAIL,
    status,
    pause: { by: 'operator', set_at: '2026-07-16T00:00:00Z' },
    ...extra,
  };
}

/** The CDK renders every menu into an overlay attached to `document.body`, not
 * inside the fixture's own element — so panel assertions query the document
 * (`kit-menu.spec.ts`'s own convention). */
const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

type MenuFixture = ReturnType<typeof TestBed.createComponent<ChunkActionsMenu>>;

/** Hand the menu a control port, and the claimant its host would derive off the route. */
function mount(fixture: MenuFixture, canControl = true): ChunkControlStub {
  const control = chunkControlStub(canControl);
  const route = fixture.componentInstance.detail().route;
  fixture.componentRef.setInput('control', control);
  fixture.componentRef.setInput('claimant', route ? runnerDisplayName(route.runner_id, route.runner_name, undefined) : null);
  return control;
}

async function openMenu(fixture: MenuFixture): Promise<void> {
  (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]')?.click();
  await fixture.whenStable();
}

describe('ChunkActionsMenu', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkActionsMenu],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('withholds the actions menu trigger without chunk:control', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture, false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
  });

  // --- Detach ---------------------------------------------

  it('shows no Detach item for a chunk with no live route', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="detach-chunk"]')).toBeNull();
  });

  it('shows a Detach item naming the runner for a chunk with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="detach-chunk"]')?.textContent).toContain('R-01.r-claude');
  });

  it('fires detach with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['detach', 'ch_01routed000000000000000000']]);
  });

  it('fires nothing when the operator declines the detach confirm', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(control.calls).toEqual([]);
  });

  it('disables Detach while the detach mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    control.pending.detach.set(true);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="detach-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    control.pending.detach.set(false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="detach-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('still shows a Detach item for a needs_human chunk that still carries a live route (not requeue)', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="detach-chunk"]')).not.toBeNull();
  });

  it('does not promise the ready queue in the confirm copy for a needs_human chunk', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ESCALATED_ROUTED_DETAIL);
    mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();

    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).not.toContain('ready queue');
  });

  // --- Complete -------------------------------------------

  it('shows a Complete item, enabled, for a running chunk with chunk:control', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('keeps Complete enabled for a stopped chunk — unlike Stop, Complete has no un-complete verb', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'stopped', pausable: false });
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('disables Complete for an already-done chunk', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'done', pausable: false, completable: false });
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).toBe('true');
  });

  it('disables Complete while the complete mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    control.pending.complete.set(true);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    control.pending.complete.set(false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="complete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('fires complete with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['complete', ROUTED_DETAIL.chunk_id]]);
  });

  it('fires nothing when the operator declines the complete confirm', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(control.calls).toEqual([]);
  });

  it('says the write cannot be undone in the complete confirm copy', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();

    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('Cannot be undone');
  });

  // --- Delete -------------------------------------------

  it('enables Delete for an unacquired chunk (not_ready, ready) with chunk:control', async () => {
    for (const status of ['not_ready', 'ready'] as const) {
      const fixture = TestBed.createComponent(ChunkActionsMenu);
      fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status, deletable: true });
      mount(fixture);
      await fixture.whenStable();
      await openMenu(fixture);

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
      const fixture = TestBed.createComponent(ChunkActionsMenu);
      fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status, delete_refusal: 'status' });
      mount(fixture);
      await fixture.whenStable();
      await openMenu(fixture);

      expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled'), status).toBe('true');
    }
  });

  it('disables Delete while the delete mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'ready', deletable: true });
    const control = mount(fixture);
    control.pending.delete.set(true);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    control.pending.delete.set(false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="delete-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('fires delete with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'ready', deletable: true });
    const control = mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['delete', ISSUE_DETAIL.chunk_id]]);
  });

  it('fires nothing when the operator declines the delete confirm', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready', deletable: true });
    const control = mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(control.calls).toEqual([]);
  });

  it('withdraws the hub items with no undo, in the delete confirm copy', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'not_ready', deletable: true });
    mount(fixture);
    const el = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();
    await openMenu(fixture);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();

    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('withdraws its hub items');
    expect(message).toContain('Cannot be undone');
  });

  // --- Delete's dependents gate ------------------------------------

  it('disables Delete and names the dependents when another chunk still depends on it', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      status: 'ready',
      deletable: true,
      delete_refusal: 'dependents',
      neighborhood: {
        prerequisites: [],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'not_ready', satisfied: false }],
      },
    });
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    const item = inOverlay('[data-testid="delete-chunk"]');
    expect(item?.getAttribute('aria-disabled')).toBe('true');
    expect(item?.textContent).toContain('Blocked: C-0ccc depend on this');
  });

  it('falls back to the plain Delete subtitle once every dependent is satisfied', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      status: 'ready',
      deletable: true,
      neighborhood: {
        prerequisites: [],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'done', satisfied: true }],
      },
    });
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    const item = inOverlay('[data-testid="delete-chunk"]');
    expect(item?.getAttribute('aria-disabled')).not.toBe('true');
    expect(item?.textContent).toContain('Remove permanently, issues stay open');
  });

  // --- Delete follows the hub's delete_refusal ------------------------------

  it("enables Delete whenever the hub reports no delete_refusal, whatever the status or dependents", async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      status: 'running',
      delete_refusal: null,
      neighborhood: {
        prerequisites: [],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'not_ready', satisfied: false }],
      },
    });
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    const item = inOverlay('[data-testid="delete-chunk"]');
    expect(item?.getAttribute('aria-disabled')).not.toBe('true');
    expect(item?.textContent).toContain('Remove permanently, issues stay open');
  });

  it('disables Delete with the plain subtitle on a status refusal, even with standing dependents', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', {
      ...ISSUE_DETAIL,
      status: 'ready',
      delete_refusal: 'status',
      neighborhood: {
        prerequisites: [],
        dependents: [{ chunk_id: 'ch_01dependent0000000000ccc', status: 'not_ready', satisfied: false }],
      },
    });
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    const item = inOverlay('[data-testid="delete-chunk"]');
    expect(item?.getAttribute('aria-disabled')).toBe('true');
    expect(item?.textContent).not.toContain('depend on this');
  });

  it('withholds Pause without chunk:control, even with a live route', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture, false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
  });

  // --- Pause / Resume -------------------------------------------

  it('shows Pause — not Resume — for a running chunk carrying no pause fact', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="pause-chunk"]')).not.toBeNull();
    expect(inOverlay('[data-testid="resume-chunk"]')).toBeNull();
  });

  it('shows no Pause for a chunk the hub would refuse to pause (done/stopped/delivering)', async () => {
    for (const status of ['done', 'stopped', 'delivering'] as const) {
      const fixture = TestBed.createComponent(ChunkActionsMenu);
      fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status, pausable: false });
      mount(fixture);
      await fixture.whenStable();
      await openMenu(fixture);

      expect(inOverlay('[data-testid="pause-chunk"]'), status).toBeNull();
      fixture.destroy();
    }
  });

  it('still offers Pause for a waiting_on_human / needs_human chunk — the lever stays broad', async () => {
    for (const status of ['waiting_on_human', 'needs_human'] as const) {
      const fixture = TestBed.createComponent(ChunkActionsMenu);
      fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status });
      mount(fixture);
      await fixture.whenStable();
      await openMenu(fixture);

      expect(inOverlay('[data-testid="pause-chunk"]'), status).not.toBeNull();
      fixture.destroy();
    }
  });

  it('shows Resume — not Pause — for a paused chunk', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    const detail = pausedDetail('paused');
    fixture.componentRef.setInput('detail', detail);
    mount(fixture);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="resume-chunk"]')).not.toBeNull();
    expect(inOverlay('[data-testid="pause-chunk"]')).toBeNull();
  });

  it('offers Resume — not Pause — for a paused chunk whose status reads waiting_on_human', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    const detail = pausedDetail('waiting_on_human');
    fixture.componentRef.setInput('detail', detail);
    const control = mount(fixture);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture);

    expect(inOverlay('[data-testid="resume-chunk"]')).not.toBeNull();
    expect(inOverlay('[data-testid="pause-chunk"]')).toBeNull();

    inOverlay('[data-testid="resume-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    expect(control.calls).toEqual([['resume', ROUTED_DETAIL.chunk_id]]);
  });

  it('fires pause with the chunk id once the operator confirms', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture);

    inOverlay('[data-testid="pause-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['pause', ROUTED_DETAIL.chunk_id]]);
  });

  it('fires nothing when the operator declines the pause confirm', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture);

    inOverlay('[data-testid="pause-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(control.calls).toEqual([]);
  });

  it('disables Pause while the pause mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    const control = mount(fixture);
    control.pending.pause.set(true);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="pause-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    control.pending.pause.set(false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="pause-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('disables Resume while the pause mutation is pending, re-enabling once it settles', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    const detail = pausedDetail('paused');
    fixture.componentRef.setInput('detail', detail);
    const control = mount(fixture);
    control.pending.pause.set(true);
    await fixture.whenStable();
    await openMenu(fixture);

    expect(inOverlay('[data-testid="resume-chunk"]')?.getAttribute('aria-disabled')).toBe('true');

    control.pending.pause.set(false);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="resume-chunk"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('fires nothing when the operator declines the resume confirm', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    const detail = pausedDetail('paused');
    fixture.componentRef.setInput('detail', detail);
    const control = mount(fixture);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture);

    inOverlay('[data-testid="resume-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(control.calls).toEqual([]);
  });

  it('does not claim the claim is given up in the pause confirm copy — that is detach', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    mount(fixture);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture);

    inOverlay('[data-testid="pause-chunk"]')?.click();

    await fixture.whenStable();
    const message = el.querySelector('[data-testid="confirm-dialog"]')?.textContent ?? '';
    expect(message).toContain('keeps the');
    expect(message).toContain('claim');
  });

  it('runs afterDelete once the port confirms the delete', async () => {
    const fixture = TestBed.createComponent(ChunkActionsMenu);
    fixture.componentRef.setInput('detail', { ...ISSUE_DETAIL, status: 'ready' });
    const control = mount(fixture);
    let after = false;
    fixture.componentRef.setInput('afterDelete', () => (after = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    await openMenu(fixture);

    inOverlay('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['delete', ISSUE_DETAIL.chunk_id]]);
    expect(after).toBe(true);
  });
});
