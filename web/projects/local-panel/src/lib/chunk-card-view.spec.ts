import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';

import { ChunkCardView } from './chunk-card-view';
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

async function render(linkedItems?: readonly runnerApi.WorkItemEntry[]) {
  await TestBed.configureTestingModule({
    imports: [ChunkCardView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkCardView);
  fixture.componentRef.setInput('lease', LEASE);
  fixture.componentRef.setInput('status', STATUS);
  if (linkedItems !== undefined) fixture.componentRef.setInput('linkedItems', linkedItems);
  fixture.detectChanges();
  await fixture.whenStable();
  return { fixture };
}

describe('ChunkCardView', () => {
  it('renders the compact ref, node, epoch, and status pill — no query stub required', async () => {
    const { fixture } = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-chunk-card"]')?.textContent).toContain('C-3YJ9');
    expect(el.querySelector('[data-testid="local-chunk-card-node"]')?.textContent).toContain('build · a2');
    expect(el.querySelector('[data-testid="local-chunk-card-status"]')?.textContent).toContain('RUNNING');
  });

  it('renders on chunk_id alone when no linked items are given', async () => {
    const { fixture } = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-chunk-card"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="local-chunk-card-title"]')?.textContent?.trim()).toBe('');
  });

  it('renders one line per linked item, each clamped to two lines with its own chip and title', async () => {
    const { fixture } = await render([
      {
        source: 'blizzard',
        ref: '61',
        label: 'blizzard#61',
        web_url: 'https://github.com/paul-gross/blizzard/issues/61',
        fetched_at: '2026-07-16T11:00:00.000Z',
        title: 'runner machine panel',
      },
      {
        source: 'blizzard',
        ref: '62',
        label: 'blizzard#62',
        web_url: 'https://github.com/paul-gross/blizzard/issues/62',
        fetched_at: '2026-07-16T11:00:00.000Z',
        title: 'mobile chunk card',
      },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    const title = el.querySelector('[data-testid="local-chunk-card-title"]');
    expect(title?.classList.contains('line2')).toBe(true);
    const lines = title?.querySelectorAll('.wi') ?? [];
    expect(lines).toHaveLength(2);

    const link1 = lines[0].querySelector<HTMLAnchorElement>('a.chip');
    expect(link1?.textContent).toContain('blizzard#61');
    expect(link1?.href).toBe('https://github.com/paul-gross/blizzard/issues/61');
    expect(lines[0].textContent).toContain('runner machine panel');
  });

  it('emits selectChunk on click, Enter, and Space', async () => {
    const { fixture } = await render();
    const emitted: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => emitted.push(id));
    const el = fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="local-chunk-card"]');

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
