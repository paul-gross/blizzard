import type { ChunkSummary, ProjectView } from 'fleet';
import { describe, expect, it } from 'vitest';

import type { EnvTimelineHost, EnvTimelineView } from './env-timeline.model';
import type { EnvTimelineSegment } from './step.model';
import { stepTooltip } from './tracks-blocks.model';
import {
  buildTracks,
  foldBelowMs,
  highlightedStep,
  presentRows,
  progressByChunk,
  projectHuesOf,
  tracksState,
  windowCovered,
  windowData,
} from './tracks.model';
import type { ResolvedTrackWindow } from './tracks-window.model';

const at = (hhmm: string): string => `2026-07-01T${hhmm}:00Z`;
const NOW = Date.parse(at('12:00'));

const seg = (start: string, end: string | null, overrides: Partial<EnvTimelineSegment> = {}): EnvTimelineSegment => ({
  chunk_id: 'ch_AAAA0001',
  cost_estimated: false,
  cost_usd: 0,
  ended_at: end === null ? null : at(end),
  epoch: 1,
  escalation: null,
  escalation_open: false,
  executor: 'runner',
  exit_choice: null,
  failed: false,
  graph_id: null,
  graph_name: 'delivery',
  lease_id: `lease_${start}`,
  node_id: 'nd_build',
  node_name: 'build',
  project_id: null,
  runner_id: 'rn_1',
  runner_name: 'r1',
  started_at: at(start),
  title: null,
  ...overrides,
});

type Layout = Record<string, Record<string, EnvTimelineSegment[]>>;

const host = (hostId: string | null, workspaces: Layout, runnerNames = ['r1'], hostName: string | null = 'xps'): EnvTimelineHost => ({
  host_id: hostId,
  host_name: hostName,
  runner_names: runnerNames,
  workspaces: Object.entries(workspaces).map(([workspace_id, envs]) => ({
    workspace_id,
    environments: Object.entries(envs).map(([environment_id, segments]) => ({ environment_id, segments })),
  })),
});

const view = (...hosts: EnvTimelineHost[]): EnvTimelineView => ({
  since: at('00:00'),
  until: at('13:00'),
  generated_at: at('12:00'),
  hosts,
});

const ids = (groups: readonly { id: string }[]): string[] => groups.map((g) => g.id);

describe('buildTracks rows', () => {
  it('has no rows for an empty view', () => {
    expect(buildTracks(view(), undefined, NOW)).toEqual({ groups: [], items: [], runnerHues: new Map() });
  });

  it('nests a host, its workspaces and their environments in display order', () => {
    const tracks = buildTracks(view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')] } }, ['r1', 'r2'])), undefined, NOW);
    expect(tracks.groups).toEqual([
      { id: 'h:hs_1', kind: 'host', label: 'xps', detail: 'r1, r2', nestedGroups: ['w:hs_1:ws1'], order: 0 },
      { id: 'w:hs_1:ws1', kind: 'workspace', label: 'ws1', detail: '', nestedGroups: ['e:hs_1:ws1/e1'], order: 1 },
      { id: 'e:hs_1:ws1/e1', kind: 'env', label: 'e1', detail: '', order: 2 },
    ]);
  });

  it('keys a host with no id as none and labels it no host', () => {
    const tracks = buildTracks(view(host(null, { ws1: { e1: [seg('10:00', '10:30')] } }, ['r1'], null)), undefined, NOW);
    expect(ids(tracks.groups)).toEqual(['h:none', 'w:none:ws1', 'e:none:ws1/e1']);
    expect(tracks.groups[0].label).toBe('no host');
  });

  it('orders environments naturally', () => {
    const steps = [seg('10:00', '10:30')];
    const tracks = buildTracks(view(host('hs_1', { ws1: { e10: steps, e2: steps, e1: steps } })), undefined, NOW);
    expect(tracks.groups.filter((g) => g.kind === 'env').map((g) => g.label)).toEqual(['e1', 'e2', 'e10']);
  });

  it('orders workspaces most recently active first, ties by name', () => {
    const tracks = buildTracks(
      view(
        host('hs_1', {
          old: { e1: [seg('08:00', '08:30')] },
          zed: { e1: [seg('10:00', '10:30')] },
          fresh: { e1: [seg('11:00', '11:30')] },
          amy: { e1: [seg('10:00', '10:30')] },
        }),
      ),
      undefined,
      NOW,
    );
    expect(tracks.groups.filter((g) => g.kind === 'workspace').map((g) => g.label)).toEqual(['fresh', 'amy', 'zed', 'old']);
  });

  it('puts a workspace running a step now above one that finished later', () => {
    const tracks = buildTracks(
      view(host('hs_1', { done: { e1: [seg('11:00', '11:50')] }, busy: { e1: [seg('08:00', null)] } })),
      undefined,
      NOW,
    );
    expect(tracks.groups.filter((g) => g.kind === 'workspace').map((g) => g.label)).toEqual(['busy', 'done']);
  });

  it('lifts a workspace the live read finds running above the window', () => {
    const windowed = view(host('hs_1', { a: { e1: [seg('11:00', '11:30')] }, b: { e1: [seg('09:00', '09:30')] } }));
    const live = view(host('hs_1', { b: { e1: [seg('09:00', null)] } }));
    const tracks = buildTracks(windowed, live, NOW);
    expect(tracks.groups.filter((g) => g.kind === 'workspace').map((g) => g.label)).toEqual(['b', 'a']);
  });
});

describe('buildTracks live-only environments', () => {
  const windowed = view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')] } }));

  it('gives a busy live-only environment a row with no blocks', () => {
    const live = view(host('hs_1', { ws1: { e2: [seg('11:00', null)] } }));
    const tracks = buildTracks(windowed, live, NOW);
    expect(ids(tracks.groups.filter((g) => g.kind === 'env'))).toEqual(['e:hs_1:ws1/e1', 'e:hs_1:ws1/e2']);
    expect(tracks.items.every((i) => i.group === 'e:hs_1:ws1/e1')).toBe(true);
  });

  it('gives an idle live-only environment no row', () => {
    const live = view(host('hs_1', { ws1: { e2: [seg('08:00', '08:30')] } }));
    expect(ids(buildTracks(windowed, live, NOW).groups)).toEqual(['h:hs_1', 'w:hs_1:ws1', 'e:hs_1:ws1/e1']);
  });

  it('gives a live-only busy environment on a new host and workspace its own header rows', () => {
    const live = view(host('hs_2', { wsN: { eN: [seg('11:00', null)] } }));
    expect(ids(buildTracks(windowed, live, NOW).groups)).toContain('e:hs_2:wsN/eN');
  });

  it('does not duplicate an environment both reads hold', () => {
    const live = view(host('hs_1', { ws1: { e1: [seg('11:00', null)] } }));
    expect(ids(buildTracks(windowed, live, NOW).groups).filter((id) => id === 'e:hs_1:ws1/e1')).toHaveLength(1);
  });
});

describe('buildTracks blocks and hues', () => {
  const steps = [seg('10:00', '10:30', { runner_name: 'zeta' }), seg('10:30', '11:00', { runner_name: 'alpha' })];

  it('hues runners in name order across rows, as tk-rN classes', () => {
    const tracks = buildTracks(view(host('hs_1', { ws1: { e1: steps } }, ['zeta', 'alpha'])), undefined, NOW);
    expect(tracks.runnerHues).toEqual(
      new Map([
        ['alpha', 'tk-r0'],
        ['zeta', 'tk-r1'],
      ]),
    );
    expect(tracks.items.map((i) => i.className.match(/tk-r\d/)?.[0])).toEqual(['tk-r1', 'tk-r0']);
  });

  it('shares the four hues round-robin among more runners', () => {
    const names = ['a', 'b', 'c', 'd', 'e'];
    const tracks = buildTracks(view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30', { runner_name: 'a' })] } }, names)), undefined, NOW);
    expect([...tracks.runnerHues!.values()]).toEqual(['tk-r0', 'tk-r1', 'tk-r2', 'tk-r3', 'tk-r0']);
  });

  it('counts runners the live read names too', () => {
    const live = view(host('hs_1', {}, ['aaa']));
    const tracks = buildTracks(view(host('hs_1', { ws1: { e1: steps } }, ['zeta'])), live, NOW);
    expect(tracks.runnerHues!.get('aaa')).toBe('tk-r0');
  });

  it('colours by project, with no runner hues, when project hues are given', () => {
    const projectHues = new Map([['proj_1', '#00ff00']]);
    const tracks = buildTracks(view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30', { project_id: 'proj_1' })] } })), undefined, NOW, projectHues);
    expect(tracks.runnerHues).toBeNull();
    expect(tracks.items[0].style).toBe('--tk-hue: #00ff00');
    expect(tracks.items[0].className).not.toMatch(/tk-r\d/);
  });

  it('folds narrow steps by the minimum block width', () => {
    const narrow = [seg('10:00', '10:01'), seg('10:01', '10:02')];
    const v = view(host('hs_1', { ws1: { e1: narrow } }));
    expect(buildTracks(v, undefined, NOW, null, null, 0).items).toHaveLength(2);
    expect(buildTracks(v, undefined, NOW, null, null, 5 * 60_000).items).toHaveLength(1);
  });

  it('marks the selected step', () => {
    const v = view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')] } }));
    const tracks = buildTracks(v, undefined, NOW, null, { chunkId: 'ch_AAAA0001', startedAt: at('10:00') });
    expect(tracks.items[0].className).toContain('tk-selected');
  });
});

describe('presentRows', () => {
  const windowed = view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')] } }));

  it('writes a host row, a workspace row and a row per environment, in buildTracks order', () => {
    const live = view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')], e2: [seg('11:00', null)] } }));
    const rows = presentRows(windowed, live, NOW);
    const tracks = buildTracks(windowed, live, NOW);
    expect(rows.map((r) => r.groupId)).toEqual(ids(tracks.groups));
    expect(rows.map((r) => r.kind)).toEqual(['host', 'workspace', 'idle', 'env']);
    expect(rows[0]).toEqual({ kind: 'host', groupId: 'h:hs_1', label: 'xps', detail: 'r1' });
  });

  it('leaves the idle time blank until a live read has landed', () => {
    expect(presentRows(windowed, undefined, NOW)[2]).toEqual({ kind: 'idle', groupId: 'e:hs_1:ws1/e1', since: '' });
  });

  it('says 14d+ for an environment the live read has no step for', () => {
    expect(presentRows(windowed, view(), NOW)[2]).toEqual({ kind: 'idle', groupId: 'e:hs_1:ws1/e1', since: '14d+' });
  });

  it('says how long an environment has sat idle since its last step ended', () => {
    const live = view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')] } }));
    expect(presentRows(windowed, live, NOW)[2]).toMatchObject({ kind: 'idle', since: '1h30m' });
  });

  it('describes a running step', () => {
    const last = seg('11:00', null, { cost_usd: 1.25, title: 'Fix it' });
    const live = view(host('hs_1', { ws1: { e1: [last] } }));
    const row = presentRows(windowed, live, NOW, new Map([['r1', 'tk-r2']]))[2];
    expect(row).toEqual({
      kind: 'env',
      groupId: 'e:hs_1:ws1/e1',
      status: 'running',
      escalation: null,
      chunkId: 'ch_AAAA0001',
      ref: 'C-0001',
      node: 'build',
      runner: 'xps/r1',
      runnerHue: 'tk-r2',
      inStep: '1h00m',
      cost: '$1.25',
      detail: stepTooltip(last, live.hosts[0], NOW),
    });
  });

  it('calls a step holding an open escalation halted', () => {
    const live = view(host('hs_1', { ws1: { e1: [seg('11:00', null, { escalation: 'budget', escalation_open: true })] } }));
    expect(presentRows(windowed, live, NOW)[2]).toMatchObject({ status: 'halted', escalation: 'budget' });
  });

  it('calls a step whose escalation was superseded running', () => {
    const live = view(host('hs_1', { ws1: { e1: [seg('11:00', null, { escalation: 'budget', escalation_open: false })] } }));
    expect(presentRows(windowed, live, NOW)[2]).toMatchObject({ status: 'running', escalation: null });
  });

  it('leaves cost blank while none is recorded and the runner hue null without a map or a hue for it', () => {
    const live = view(host('hs_1', { ws1: { e1: [seg('11:00', null)] } }));
    expect(presentRows(windowed, live, NOW)[2]).toMatchObject({ cost: '', runnerHue: null });
    expect(presentRows(windowed, live, NOW, new Map())[2]).toMatchObject({ runnerHue: null });
  });

  it('reads the present from the live read alone, not the window', () => {
    const wide = view(host('hs_1', { ws1: { e1: [seg('11:00', null)] } }));
    const live = view(host('hs_1', { ws1: { e1: [seg('11:30', '11:45')] } }));
    expect(presentRows(wide, live, NOW)[2]).toMatchObject({ kind: 'idle', since: '15m' });
  });
});

describe('window selection', () => {
  const window = (overrides: Partial<ResolvedTrackWindow>): ResolvedTrackWindow => ({
    startMs: Date.parse(at('06:00')),
    endMs: Date.parse(at('12:00')),
    since: at('06:00'),
    until: undefined,
    live: false,
    ...overrides,
  });
  const live = view();
  const range = { ...view(), generated_at: at('09:00') };

  it('covers a following window always', () => {
    expect(windowCovered(window({ live: true }), undefined)).toBe(true);
  });

  it('covers a pinned window inside the live read, not before it nor without one', () => {
    expect(windowCovered(window({}), { ...live, since: at('05:00') })).toBe(true);
    expect(windowCovered(window({}), { ...live, since: at('06:00') })).toBe(true);
    expect(windowCovered(window({}), { ...live, since: at('07:00') })).toBe(false);
    expect(windowCovered(window({}), undefined)).toBe(false);
  });

  it('draws from the live read while it covers, else the pinned range', () => {
    expect(windowData(true, live, range)).toBe(live);
    expect(windowData(false, live, range)).toBe(range);
    expect(windowData(false, live, undefined)).toBeUndefined();
  });
});

describe('tracksState', () => {
  const query = (pending: boolean, error: boolean) => ({ isPending: () => pending, isError: () => error });
  const idle = query(false, false);
  const full = view(host('hs_1', { ws1: { e1: [seg('10:00', '10:30')] } }));

  it('is loading while the read it draws from is pending', () => {
    expect(tracksState(true, query(true, false), idle, view(), undefined)).toBe('loading');
    expect(tracksState(true, idle, query(true, false), view(), undefined)).toBe('empty');
  });

  it('reads the range query when not covered', () => {
    expect(tracksState(false, query(true, false), query(false, true), view(), undefined)).toBe('error');
  });

  it('is empty only when neither the window nor the present holds a host', () => {
    expect(tracksState(true, idle, idle, view(), undefined)).toBe('empty');
    expect(tracksState(true, idle, idle, view(), view())).toBe('empty');
    expect(tracksState(true, idle, idle, view(), full)).toBe('ready');
    expect(tracksState(true, idle, idle, full, undefined)).toBe('ready');
  });
});

describe('progressByChunk', () => {
  it('maps each chunk to its graph progress', () => {
    const progress = [{ node_id: 'nd_1', name: 'plan', state: 'done' }];
    const chunks = [{ chunk_id: 'ch_1', graph_progress: progress }] as unknown as ChunkSummary[];
    expect(progressByChunk(chunks).get('ch_1')).toBe(progress);
  });

  it('is empty before the chunks read', () => {
    expect(progressByChunk(undefined).size).toBe(0);
  });
});

describe('projectHuesOf', () => {
  const project = (project_id: string, color: string | null): ProjectView => ({ project_id, color }) as unknown as ProjectView;

  it('is null while blocks wear their runners colour', () => {
    expect(projectHuesOf(false, [project('proj_1', 'blue')])).toBeNull();
  });

  it('is empty before the projects read', () => {
    expect(projectHuesOf(true, undefined)).toEqual(new Map());
  });

  it('tints each coloured project, skipping uncoloured ones', () => {
    const hues = projectHuesOf(true, [project('proj_1', '#ABCDEF'), project('proj_2', null), project('proj_3', 'green')]);
    expect([...hues!.entries()]).toEqual([
      ['proj_1', '#abcdef'],
      ['proj_3', 'var(--project-green, var(--label-dim))'],
    ]);
  });
});

describe('highlightedStep', () => {
  it('highlights nothing without a ref', () => {
    expect(highlightedStep(null, seg('10:00', null))).toBeNull();
  });

  it('highlights the whole chunk until the read lands on it', () => {
    expect(highlightedStep({ chunkId: 'ch_AAAA0001', session: null, at: null }, undefined)).toEqual({ chunkId: 'ch_AAAA0001', startedAt: '' });
    expect(highlightedStep({ chunkId: 'ch_AAAA0001', session: null, at: null }, seg('10:00', null, { chunk_id: 'ch_OTHER' }))).toEqual({
      chunkId: 'ch_AAAA0001',
      startedAt: '',
    });
  });

  it('highlights the step the read resolved on the same chunk', () => {
    expect(highlightedStep({ chunkId: 'ch_AAAA0001', session: 'lease_x', at: null }, seg('10:00', null))).toEqual({
      chunkId: 'ch_AAAA0001',
      startedAt: at('10:00'),
    });
  });
});

describe('foldBelowMs', () => {
  it('is zero before the scale is known', () => {
    expect(foldBelowMs(0, 10)).toBe(0);
    expect(foldBelowMs(-5, 10)).toBe(0);
    expect(foldBelowMs(5, 0)).toBe(0);
  });

  it('buckets to the next power of two', () => {
    expect(foldBelowMs(100, 10)).toBe(1024);
    expect(foldBelowMs(1024, 1)).toBe(1024);
    expect(foldBelowMs(1025, 1)).toBe(2048);
    expect(foldBelowMs(0.3, 1)).toBe(0.5);
  });

  it('holds across a zoom that does not double or halve the scale', () => {
    expect(foldBelowMs(70, 10)).toBe(foldBelowMs(90, 10));
  });
});
