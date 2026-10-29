import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { workItemEntry } from 'fleet/testing';

import { type MachineChunkRow, machineChunkRows } from '../../core/chunk-rows.model';
import type { PanelLease } from '../../core/panel-reads.model';
import { ChunkRowView } from './chunk-row-view';

const LEASE = (overrides: Partial<PanelLease> = {}): PanelLease => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  display_status: 'running',
  closure_healthy: false,
  closed_at: null,
  closure_reason: null,
  stale_after_seconds: 3600,
  resolved_model: null,
  resolved_effort: null,
  harness_version: null,
  git_commits: [],
  harness_id: null,
  project_id: null,
  owner: { runnerId: 'rn_1', name: 'r-claude', label: 'r-claude' },
  projectSlug: 'blizzard',
  ...overrides,
});

/** The chunk's folded row, as the board's container folds it. */
function rowOf(overrides: Partial<PanelLease> = {}): MachineChunkRow {
  return machineChunkRows([LEASE(overrides)], [{ slug: 'blizzard', name: 'Blizzard', entries: ['blizzard'], color: 'cyan' }])[0];
}

async function render(
  inputs: Partial<{ row: MachineChunkRow; projectTint: string | null; linkedItems: readonly runnerApi.WorkItemEntry[]; selected: boolean }> = {},
) {
  await TestBed.configureTestingModule({
    imports: [ChunkRowView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkRowView);
  fixture.componentRef.setInput('row', inputs.row ?? rowOf());
  if (inputs.projectTint !== undefined) fixture.componentRef.setInput('projectTint', inputs.projectTint);
  if (inputs.selected !== undefined) fixture.componentRef.setInput('selected', inputs.selected);
  if (inputs.linkedItems !== undefined) fixture.componentRef.setInput('linkedItems', inputs.linkedItems);
  fixture.detectChanges();
  await fixture.whenStable();
  const el = fixture.nativeElement as HTMLElement;
  return { fixture, el, card: el.querySelector<HTMLElement>('[data-testid="chunk-row"]')! };
}

describe('ChunkRowView', () => {
  it('renders the step row — runner · node and the short id — off plain inputs, no query stub required', async () => {
    const { el, card } = await render();

    expect(card.getAttribute('data-chunk-id')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(card.getAttribute('data-lane')).toBe('progress');
    expect(el.querySelector('[data-testid="chunk-row-runner"]')?.textContent?.trim()).toBe('r-claude');
    expect(el.querySelector('[data-testid="chunk-row-node"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('r-claude · build');
    expect(el.querySelector('[data-testid="chunk-id"]')?.textContent?.trim()).toBe('C-3YJ9');
  });

  it('carries the live worker inline on one line — its environment and state, with no heartbeat tag', async () => {
    const { el } = await render();
    const worker = el.querySelector<HTMLElement>('[data-testid="chunk-row-worker"]');

    expect(worker).not.toBeNull();
    expect(worker?.querySelector('[data-testid="worker-env"]')?.textContent?.trim()).toBe('beta');
    expect(worker?.querySelector('[data-testid="worker-state"]')?.textContent?.trim()).toBe('RUNNING');
    // The step row already names the node, so the worker line leads with its environment.
    expect(worker?.querySelector('[data-testid="worker-node"]')).toBeNull();
    expect(worker?.textContent).not.toMatch(/\bhb\b/);
  });

  it("names no status while it only repeats the live worker's own state", async () => {
    const { el } = await render();

    expect(el.querySelector('[data-testid="chunk-row-status"]')).toBeNull();
  });

  it('names a human fact that outranks the live worker', async () => {
    const { el } = await render({ row: rowOf({ state: 'parked', display_status: 'waiting-ask' }) });

    expect(el.querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('WAITING · ASK');
  });

  it('names the status and drops the worker line once the session ended', async () => {
    const { el, card } = await render({
      row: rowOf({ state: 'closed', display_status: 'closed', closure_healthy: true, closed_at: '2026-07-16T11:30:00.000Z', closure_reason: 'transitioned' }),
    });

    expect(el.querySelector('[data-testid="chunk-row-worker"]')).toBeNull();
    expect(el.querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('COMPLETED');
    expect(card.getAttribute('data-lane')).toBe('done');
  });

  it("tints the card with its project's colour and names the project on hover", async () => {
    const { card } = await render({ projectTint: 'var(--project-cyan, var(--label-dim))' });

    expect(card.classList).toContain('tinted');
    expect(card.style.getPropertyValue('--project-tint')).toBe('var(--project-cyan, var(--label-dim))');
    expect(card.getAttribute('title')).toBe('Blizzard');
  });

  it('leaves a card with no project colour untinted', async () => {
    const { card } = await render({ projectTint: null });

    expect(card.classList).not.toContain('tinted');
    expect(card.style.getPropertyValue('--project-tint')).toBe('');
  });

  it('renders on chunk_id alone when no linked items are given', async () => {
    const { el, card } = await render();

    expect(card).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-row-title"]')).toBeNull();
  });

  it('renders one line per linked item, its label over its title', async () => {
    const { el } = await render({
      linkedItems: [
        workItemEntry({
          source: 'blizzard',
          ref: '61',
          label: 'blizzard#61',
          web_url: 'https://github.com/paul-gross/blizzard/issues/61',
          fetched_at: '2026-07-16T11:00:00.000Z',
          title: 'runner machine panel',
        }),
      ],
    });

    const items = el.querySelectorAll('[data-testid="chunk-row-title"] .item');
    expect(items).toHaveLength(1);
    expect(items[0].querySelector('.iss')?.textContent?.trim()).toBe('blizzard#61');
    expect(items[0].querySelector('.title')?.textContent?.trim()).toBe('runner machine panel');
  });

  it('marks the selected card', async () => {
    const { card } = await render({ selected: true });

    expect(card.classList).toContain('selected');
  });

  it('emits selectChunk on click, Enter, and Space', async () => {
    const { fixture, card } = await render();
    const emitted: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => emitted.push(id));

    card.click();
    card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));

    expect(emitted).toEqual([
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    ]);
  });
});
