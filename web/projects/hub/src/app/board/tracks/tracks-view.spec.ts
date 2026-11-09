import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { settle } from 'fleet/testing';
import type { NodeBlock } from '../node-strip/node-strip.model';
import type { PresentRow, Tracks } from './tracks.model';
import { TracksView } from './tracks-view';

/** A stand-in for vis's Timeline: laying out groups appends each group's label element under `.vis-label`, as vis does,
 * so the view can measure the rows it heads. */
vi.mock('vis-timeline/standalone', () => {
  class Timeline {
    constructor(private readonly container: HTMLElement) {}
    setGroups(groups: readonly { content: HTMLElement }[]): void {
      for (const group of groups) {
        const label = document.createElement('div');
        label.className = 'vis-label';
        label.append(group.content);
        this.container.append(label);
      }
    }
    on = vi.fn();
    addCustomTime = vi.fn();
    setCustomTime = vi.fn();
    getWindow(): { start: Date; end: Date } {
      return { start: new Date(0), end: new Date(1000) };
    }
    setWindow = vi.fn();
    destroy = vi.fn();
  }
  class DataSet {
    update = vi.fn();
    remove = vi.fn();
  }
  return { Timeline, DataSet };
});

const EMPTY: Tracks = { groups: [], items: [], runnerHues: null };

const GROUPS: Tracks['groups'] = [
  { id: 'host_1', kind: 'host', label: 'xps', detail: '', order: 0 },
  { id: 'env_a', kind: 'env', label: 'env_a', detail: '', order: 1 },
  { id: 'env_b', kind: 'env', label: 'env_b', detail: '', order: 2 },
  { id: 'env_c', kind: 'env', label: 'env_c', detail: '', order: 3 },
];

const running = (over: Partial<Extract<PresentRow, { kind: 'env' }>> = {}): PresentRow => ({
  kind: 'env',
  groupId: 'env_a',
  status: 'running',
  escalation: null,
  chunkId: 'ch_01',
  ref: 'ch_01',
  node: 'build',
  runner: 'runner-one',
  runnerHue: null,
  inStep: '12m',
  cost: '$1.20',
  detail: 'ch_01 on build',
  ...over,
});

const PRESENT: readonly PresentRow[] = [
  { kind: 'host', groupId: 'host_1', label: 'xps', detail: '' },
  running(),
  { kind: 'idle', groupId: 'env_b', since: '3h' },
  { kind: 'idle', groupId: 'env_c', since: '' },
];

describe('TracksView', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class { observe = vi.fn(); disconnect = vi.fn(); unobserve = vi.fn(); });
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  });
  afterEach(() => vi.unstubAllGlobals());

  async function render(inputs: Record<string, unknown> = {}) {
    const fixture = TestBed.createComponent(TracksView);
    fixture.componentRef.setInput('tracks', EMPTY);
    fixture.componentRef.setInput('present', []);
    fixture.componentRef.setInput('startMs', 0);
    fixture.componentRef.setInput('endMs', 1000);
    fixture.componentRef.setInput('nowMs', 500);
    for (const [name, value] of Object.entries(inputs)) fixture.componentRef.setInput(name, value);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  const q = (el: HTMLElement, testid: string) => el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);

  /** Let the dynamic import land, then the frame that measures the rows. */
  async function measured(fixture: Awaited<ReturnType<typeof render>>['fixture']) {
    await vi.waitFor(() => expect(fixture.nativeElement.querySelector('[data-gid]')).not.toBeNull());
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    await settle(fixture);
  }

  it('offers the span presets and emits the chosen one', async () => {
    const { fixture, el } = await render({ span: '24h' });
    const chosen: string[] = [];
    fixture.componentInstance.chooseSpan.subscribe((value) => chosen.push(value));

    expect(['6h', '24h', '3d', '7d'].map((v) => q(el, `tracks-span-${v}`) !== null)).toEqual([true, true, true, true]);
    q(el, 'tracks-span-3d')!.click();

    expect(chosen).toEqual(['3d']);
  });

  it('says it follows now while live, and offers the way back once pinned', async () => {
    const { el } = await render();
    expect(el.querySelector('.tk-follow')?.textContent).toContain('following now');
    expect(q(el, 'tracks-follow-now')).toBeNull();

    const { fixture, el: pinned } = await render({ live: false });
    let followed = 0;
    fixture.componentInstance.followNow.subscribe(() => followed++);
    expect(pinned.querySelector('.tk-follow')).toBeNull();
    q(pinned, 'tracks-follow-now')!.click();

    expect(followed).toBe(1);
  });

  describe('runner key', () => {
    const hues = (n: number): Tracks => ({
      ...EMPTY,
      runnerHues: new Map(Array.from({ length: n }, (_, i) => [`runner-${i}`, `tk-r${i}`] as const)),
    });

    it('names one to five runners', async () => {
      const { el } = await render({ tracks: hues(5) });

      expect([...q(el, 'tracks-runner-key')!.querySelectorAll('.tk-key-item')].map((i) => i.textContent)).toEqual([
        'runner-0',
        'runner-1',
        'runner-2',
        'runner-3',
        'runner-4',
      ]);
    });

    it('is left off for six runners, for none, and when blocks wear project colours', async () => {
      expect(q((await render({ tracks: hues(6) })).el, 'tracks-runner-key')).toBeNull();
      expect(q((await render({ tracks: hues(0) })).el, 'tracks-runner-key')).toBeNull();
      expect(q((await render()).el, 'tracks-runner-key')).toBeNull();
    });
  });

  describe('async state', () => {
    it.each([
      ['loading', 'tracks-loading', 'Loading tracks…'],
      ['error', 'tracks-error', 'FAILED TO LOAD TRACKS'],
      ['empty', 'tracks-empty', 'NO ENVIRONMENT ACTIVITY IN THIS WINDOW'],
    ] as const)('shows the %s text', async (state, testid, text) => {
      const { el } = await render({ state });

      expect(q(el, testid)?.textContent).toContain(text);
      expect(el.querySelector('.tk-body')?.classList).toContain('tk-hidden');
    });

    it('shows no resting text, and the body, when ready', async () => {
      const { el } = await render({ state: 'ready' });

      expect(q(el, 'tracks-loading')).toBeNull();
      expect(q(el, 'tracks-error')).toBeNull();
      expect(q(el, 'tracks-empty')).toBeNull();
      expect(el.querySelector('.tk-body')?.classList).not.toContain('tk-hidden');
    });
  });

  describe('present column', () => {
    it('draws no row for a group whose label has not been measured', async () => {
      const { el } = await render({ present: PRESENT });

      expect(q(el, 'tracks-present')!.querySelectorAll('.tk-row')).toHaveLength(0);
    });

    it('heads the host row with now, and the empty rows with idle', async () => {
      const { fixture, el } = await render({ tracks: { ...EMPTY, groups: GROUPS }, present: PRESENT });
      await measured(fixture);

      const rows = [...q(el, 'tracks-present')!.querySelectorAll<HTMLElement>('.tk-row')];
      expect(rows).toHaveLength(4);
      expect(rows[0].querySelector('.tk-now-head')?.textContent).toBe('now');
      expect(rows[2].textContent?.trim()).toBe('idle · 3h');
      expect(rows[3].textContent?.trim()).toBe('idle');
    });

    it('lays an environment row out with its ref, node, hold, runner and cost', async () => {
      const { fixture, el } = await render({ tracks: { ...EMPTY, groups: GROUPS }, present: PRESENT });
      await measured(fixture);

      const row = q(el, 'tracks-present')!.querySelectorAll<HTMLElement>('.tk-row')[1];
      expect(row.querySelector('.tk-ref')?.textContent).toBe('ch_01');
      expect(row.querySelector('.tk-node')?.textContent).toBe('build');
      expect(row.querySelector('.tk-held')?.textContent).toBe('12m');
      expect(row.querySelector('.tk-held')?.classList).not.toContain('tk-halted');
      expect(row.querySelector('.tk-runner')?.textContent).toBe('runner-one');
      expect(row.querySelector('.tk-cost')?.textContent).toBe('$1.20');
    });

    it('marks a halted environment with its escalation', async () => {
      const halted = running({ status: 'halted', escalation: 'stuck' });
      const { fixture, el } = await render({ tracks: { ...EMPTY, groups: GROUPS }, present: [halted] });
      await measured(fixture);

      const row = q(el, 'tracks-present')!.querySelector<HTMLElement>('.tk-row')!;
      expect(row.querySelector('.tk-held')?.textContent).toBe('halted · stuck');
      expect(row.querySelector('.tk-held')?.classList).toContain('tk-halted');
      expect(row.querySelector('.tk-ref')?.classList).toContain('tk-halted');
      expect(row.querySelector('.tk-dot')?.classList).toContain('tk-dot-halted');
    });

    it('hands the node strip the progress of its chunk, and none for an unknown one', async () => {
      const blocks: NodeBlock[] = [
        { node_id: 'nd_plan', name: 'plan', state: 'passed' },
        { node_id: 'nd_build', name: 'build', state: 'current' },
      ];
      const present = [running(), running({ groupId: 'env_b', chunkId: 'ch_other' })];
      const { fixture, el } = await render({
        tracks: { ...EMPTY, groups: GROUPS },
        present,
        progress: new Map([['ch_01', blocks]]),
      });
      await measured(fixture);

      const strips = [...q(el, 'tracks-present')!.querySelectorAll('app-node-strip')];
      expect(strips.map((s) => s.querySelectorAll('.block').length)).toEqual([2, 0]);
    });
  });
});
