import type { hubApi } from 'fleet';
import { describe, expect, it, vi } from 'vitest';

import {
  type EnvTimelineHost,
  type EnvTimelineView,
  FULL_READ_EVERY_MS,
  type LiveTimeline,
  NO_TIMELINE,
  catchUp,
  expandTimeline,
  hubNow,
  mergeLatest,
  mergeWindow,
} from './env-timeline.model';
import type { EnvTimelineSegment } from './step.model';

const at = (hhmm: string): string => `2026-07-01T${hhmm}:00Z`;

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

const host = (
  hostId: string | null,
  workspaces: Record<string, Record<string, EnvTimelineSegment[]>>,
): EnvTimelineHost => ({
  host_id: hostId,
  host_name: null,
  runner_names: ['r1'],
  workspaces: Object.entries(workspaces).map(([workspace_id, envs]) => ({
    workspace_id,
    environments: Object.entries(envs).map(([environment_id, segments]) => ({ environment_id, segments })),
  })),
});

const view = (hosts: EnvTimelineHost[], since = at('09:00'), until = at('12:00'), generated = at('11:00')): EnvTimelineView => ({
  since,
  until,
  generated_at: generated,
  hosts,
});

const startsOf = (v: EnvTimelineView, hostId: string | null, ws: string, env: string): string[] =>
  v.hosts
    .find((h) => h.host_id === hostId)!
    .workspaces.find((w) => w.workspace_id === ws)!
    .environments.find((e) => e.environment_id === env)!
    .segments.map((s) => s.started_at);

describe('hubNow', () => {
  it('reads the hubs generated_at', () => {
    expect(hubNow(view([]), 5)).toBe(Date.parse(at('11:00')));
  });

  it('falls back before any answer', () => {
    expect(hubNow(undefined, 5)).toBe(5);
    expect(hubNow(NO_TIMELINE, 5)).toBe(5);
  });
});

describe('expandTimeline', () => {
  const block = (overrides: Partial<hubApi.EnvTimelineBlockView> = {}): hubApi.EnvTimelineBlockView => ({
    chunk_id: 'ch_AAAA0001',
    cost_estimated: false,
    cost_usd: 1,
    ended_at: at('10:10'),
    epoch: 2,
    escalation: null,
    escalation_open: false,
    executor: 'runner',
    exit_choice: 'pass',
    failed: false,
    lease_id: 'lease_1',
    node_id: 'nd_build',
    runner_id: 'rn_1',
    started_at: at('10:00'),
    ...overrides,
  });

  const wire = (blocks: hubApi.EnvTimelineBlockView[]): hubApi.EnvTimelineView => ({
    since: at('09:00'),
    until: at('12:00'),
    generated_at: at('11:00'),
    chunks: { ch_AAAA0001: { title: 'Fix it', project_id: 'proj_1' } },
    nodes: { nd_build: { graph_name: 'delivery', node_name: 'build' } },
    runners: { rn_1: 'alpha' },
    hosts: [
      {
        host_id: 'hs_1',
        host_name: 'xps',
        runner_names: ['alpha'],
        workspaces: [{ workspace_id: 'ws1', environments: [{ environment_id: 'e1', blocks }] }],
      },
    ],
  });

  it('lays the chunk, node and runner names back onto each block', () => {
    const out = expandTimeline(wire([block()]));
    expect(out.hosts[0].workspaces[0].environments[0].segments).toEqual([
      { ...block(), title: 'Fix it', project_id: 'proj_1', runner_name: 'alpha', graph_id: null, graph_name: 'delivery', node_name: 'build' },
    ]);
  });

  it('keeps the window, the host and the environment ids', () => {
    const out = expandTimeline(wire([block()]));
    expect(out).toMatchObject({ since: at('09:00'), until: at('12:00'), generated_at: at('11:00') });
    expect(out.hosts[0]).toMatchObject({ host_id: 'hs_1', host_name: 'xps', runner_names: ['alpha'] });
    expect(out.hosts[0].workspaces[0].workspace_id).toBe('ws1');
    expect(out.hosts[0].workspaces[0].environments[0].environment_id).toBe('e1');
  });

  it('falls back to the ids when a table lacks them', () => {
    const [segment] = expandTimeline(wire([block({ chunk_id: 'ch_ZZZZ', node_id: 'nd_missing', runner_id: 'rn_missing' })])).hosts[0]
      .workspaces[0].environments[0].segments;
    expect(segment).toMatchObject({
      title: null,
      project_id: null,
      graph_name: null,
      node_name: 'nd_missing',
      runner_name: 'rn_missing',
    });
  });
});

describe('mergeWindow', () => {
  const previous = view([
    host('hs_1', {
      ws1: {
        e1: [seg('10:00', '10:10'), seg('10:10', '10:30'), seg('10:30', null)],
        e2: [seg('09:00', '09:30')],
      },
    }),
  ]);

  it('keeps the steps that ended by the deltas since and lays the deltas steps over the rest', () => {
    const delta = view([host('hs_1', { ws1: { e1: [seg('10:10', '10:30', { cost_usd: 3 }), seg('10:30', '10:50')] } })], at('10:20'));
    const merged = mergeWindow(previous, delta);
    expect(startsOf(merged, 'hs_1', 'ws1', 'e1')).toEqual([at('10:00'), at('10:10'), at('10:30')]);
    const replaced = merged.hosts[0].workspaces[0].environments[0].segments;
    expect(replaced[1].cost_usd).toBe(3);
    expect(replaced[2].ended_at).toBe(at('10:50'));
  });

  it('keeps a step that ended exactly at the cut', () => {
    const delta = view([host('hs_1', { ws1: { e1: [seg('10:30', null)] } })], at('10:10'));
    expect(startsOf(mergeWindow(previous, delta), 'hs_1', 'ws1', 'e1')).toEqual([at('10:00'), at('10:30')]);
  });

  it('sorts the steps by start', () => {
    const delta = view([host('hs_1', { ws1: { e1: [seg('09:50', '10:05')] } })], at('09:40'));
    const merged = mergeWindow(
      view([host('hs_1', { ws1: { e1: [seg('09:00', '09:30'), seg('11:00', '11:10')] } })]),
      delta,
    );
    expect(startsOf(merged, 'hs_1', 'ws1', 'e1')).toEqual([at('09:00'), at('09:50')]);
  });

  it('leaves environments the delta does not name as they were, while their steps ended before the cut', () => {
    const delta = view([host('hs_1', { ws1: { e1: [seg('10:30', null)] } })], at('10:20'));
    expect(startsOf(mergeWindow(previous, delta), 'hs_1', 'ws1', 'e2')).toEqual([at('09:00')]);
  });

  it('drops an environment, workspace and host left with no step', () => {
    const delta = view([], at('09:45'));
    const merged = mergeWindow(
      view([host('hs_1', { ws1: { e1: [seg('10:00', null)] }, ws2: { e1: [seg('09:00', '09:30')] } }), host('hs_2', { ws1: { e1: [seg('10:00', null)] } })]),
      delta,
    );
    expect(merged.hosts.map((h) => h.host_id)).toEqual(['hs_1']);
    expect(merged.hosts[0].workspaces.map((w) => w.workspace_id)).toEqual(['ws2']);
  });

  it('adds a host, workspace and environment the previous read lacked', () => {
    const delta = view([host('hs_9', { wsN: { eN: [seg('10:40', null)] } })], at('10:20'));
    expect(startsOf(mergeWindow(previous, delta), 'hs_9', 'wsN', 'eN')).toEqual([at('10:40')]);
  });

  it('takes the deltas until and generated_at and the previous since', () => {
    const delta = view([], at('10:20'), at('13:00'), at('12:30'));
    expect(mergeWindow(previous, delta)).toMatchObject({ since: at('09:00'), until: at('13:00'), generated_at: at('12:30') });
  });
});

describe('mergeLatest', () => {
  const previous = view([host('hs_1', { ws1: { e1: [seg('10:00', '10:10')], e2: [seg('10:00', '10:20')] } })]);

  it('replaces the track of an environment the delta names', () => {
    const delta = view([host('hs_1', { ws1: { e1: [seg('10:30', null)] } })], at('10:00'), at('13:00'), at('12:00'));
    const merged = mergeLatest(previous, delta);
    expect(startsOf(merged, 'hs_1', 'ws1', 'e1')).toEqual([at('10:30')]);
    expect(startsOf(merged, 'hs_1', 'ws1', 'e2')).toEqual([at('10:00')]);
    expect(merged).toMatchObject({ since: at('09:00'), until: at('13:00'), generated_at: at('12:00') });
  });

  it('ignores an environment the delta names with no step', () => {
    const delta = view([host('hs_1', { ws1: { e1: [] } })]);
    expect(startsOf(mergeLatest(previous, delta), 'hs_1', 'ws1', 'e1')).toEqual([at('10:00')]);
  });

  it('adds environments in hosts the previous read lacked', () => {
    const delta = view([host(null, { ws1: { e1: [seg('10:40', null)] } })]);
    expect(startsOf(mergeLatest(previous, delta), null, 'ws1', 'e1')).toEqual([at('10:40')]);
  });
});

describe('catchUp', () => {
  const NOW = Date.parse(at('11:30'));
  const whole = vi.fn();
  const since = vi.fn();
  const merge = vi.fn();
  const wholeView = view([host('hs_1', { ws1: { e1: [seg('10:00', null)] } })]);
  const deltaView = view([], at('10:58'));
  const mergedView = view([host('hs_1', { ws1: { e1: [seg('10:00', '11:20')] } })]);

  const prior = (readWholeAt: number): LiveTimeline => ({ ...view([], at('09:00'), at('12:00'), at('11:00')), readWholeAt });

  const reset = (): void => {
    whole.mockReset().mockResolvedValue(wholeView);
    since.mockReset().mockResolvedValue(deltaView);
    merge.mockReset().mockReturnValue(mergedView);
  };

  it('reads whole the first time', async () => {
    reset();
    const out = await catchUp(undefined, NOW, whole, since, merge);
    expect(out).toEqual({ ...wholeView, readWholeAt: NOW });
    expect(since).not.toHaveBeenCalled();
  });

  it('asks only for what moved since the last answer, less two minutes of overlap', async () => {
    reset();
    const previous = prior(NOW - 60_000);
    const out = await catchUp(previous, NOW, whole, since, merge);
    expect(since).toHaveBeenCalledWith(at('10:58').replace(':00Z', ':00.000Z'));
    expect(merge).toHaveBeenCalledWith(previous, deltaView);
    expect(whole).not.toHaveBeenCalled();
    expect(out).toEqual({ ...mergedView, readWholeAt: NOW - 60_000 });
  });

  it('keeps catching up at exactly the full-read interval', async () => {
    reset();
    await catchUp(prior(NOW - FULL_READ_EVERY_MS), NOW, whole, since, merge);
    expect(whole).not.toHaveBeenCalled();
  });

  it('starts over whole once the last whole read is older than the interval', async () => {
    reset();
    const out = await catchUp(prior(NOW - FULL_READ_EVERY_MS - 1), NOW, whole, since, merge);
    expect(since).not.toHaveBeenCalled();
    expect(out.readWholeAt).toBe(NOW);
  });
});
