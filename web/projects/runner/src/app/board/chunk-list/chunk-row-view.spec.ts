import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';

import { ChunkRowView } from './chunk-row-view';
import type { MachineChunkStatus } from './chunk-status';

const LEASE: runnerApi.LeaseView = {
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
  closed_at: null,
  closure_reason: null,
};

const STATUS: MachineChunkStatus = { label: 'RUNNING', tone: 'running' };

async function render(overrides: Partial<{ linkedItems: readonly runnerApi.WorkItemEntry[]; selected: boolean }> = {}) {
  await TestBed.configureTestingModule({
    imports: [ChunkRowView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkRowView);
  fixture.componentRef.setInput('lease', LEASE);
  fixture.componentRef.setInput('status', STATUS);
  if (overrides.selected !== undefined) fixture.componentRef.setInput('selected', overrides.selected);
  if (overrides.linkedItems !== undefined) fixture.componentRef.setInput('linkedItems', overrides.linkedItems);
  fixture.detectChanges();
  await fixture.whenStable();
  return { fixture };
}

describe('ChunkRowView', () => {
  it('renders every field the container hands it — no query stub required', async () => {
    const { fixture } = await render();
    const el = fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="chunk-row"]');

    expect(card?.textContent).toContain('C-3YJ9');
    expect(card?.textContent).toContain('build · a2');
    expect(el.querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('RUNNING');
  });

  it("colors the card's left edge with the derived status's own tone", async () => {
    const { fixture } = await render();
    fixture.componentRef.setInput('status', { label: 'NEEDS HUMAN', tone: 'needs' });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="chunk-row"]');

    expect(card?.style.borderLeftColor).toBe('var(--red)');
  });

  it('renders on chunk_id alone when no linked items are given', async () => {
    const { fixture } = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-row"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-row-title"]')?.textContent?.trim()).toBe('');
  });

  it('renders one line per linked item, each with its own chip and its own title', async () => {
    const { fixture } = await render({
      linkedItems: [
        {
          source: 'blizzard',
          ref: '61',
          label: 'blizzard#61',
          web_url: 'https://github.com/paul-gross/blizzard/issues/61',
          fetched_at: '2026-07-16T11:00:00.000Z',
          title: 'runner machine panel',
        },
      ],
    });
    const el = fixture.nativeElement as HTMLElement;

    const title = el.querySelector('[data-testid="chunk-row-title"]');
    const lines = title?.querySelectorAll('.wi') ?? [];
    expect(lines).toHaveLength(1);
    const link = lines[0].querySelector<HTMLAnchorElement>('a.chip');
    expect(link?.textContent).toContain('blizzard#61');
    expect(link?.href).toBe('https://github.com/paul-gross/blizzard/issues/61');
    expect(lines[0].textContent).toContain('runner machine panel');
  });

  it('emits selectChunk on click, Enter, and Space', async () => {
    const { fixture } = await render();
    const emitted: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => emitted.push(id));
    const el = fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="chunk-row"]');

    card?.click();
    card?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    card?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));

    expect(emitted).toEqual([
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    ]);
  });
});
