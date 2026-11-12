import type { ChunkDetail, ProjectView } from 'fleet';
import { describe, expect, it } from 'vitest';

import type { EnvTimelineSegment, PlacedStep } from './step.model';
import type { EnvTimelineStepView } from './step.query';
import {
  chunkFor,
  drawerNow,
  drawerOrigin,
  journeyBlocks,
  localStamp,
  neighbours,
  ownArtifactKeys,
  placedOf,
  projectOf,
  stepArtifactsOf,
  stepFacts,
  stepIdentity,
  stepModel,
  transcriptRefOf,
} from './step-drawer.model';

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

const placed = (segment: EnvTimelineSegment, workspace = 'ws1', environment = 'e1'): PlacedStep => ({
  environment_id: environment,
  host_id: 'hs_1',
  host_name: 'xps',
  segment,
  workspace_id: workspace,
});

type Invocation = EnvTimelineStepView['invocations'][number];

const inv = (overrides: Partial<Invocation> = {}): Invocation => ({
  cache_create_tokens: 0,
  cache_read_tokens: 0,
  cost_usd: 0,
  estimated_cost_usd: null,
  harness_id: null,
  input_tokens: 0,
  kind: 'resume',
  model: null,
  output_tokens: 0,
  recorded_at: at('10:00'),
  ...overrides,
});

const stepView = (journey: PlacedStep[], invocations: Invocation[] = [], generated = at('11:00')): EnvTimelineStepView => ({
  artifact_keys: [],
  escalations: [],
  generated_at: generated,
  invocations,
  spend: null,
  journey,
  step: journey[journey.length - 1],
});

const first = placed(seg('10:00', '10:10'));
const second = placed(seg('10:20', '10:30', { chunk_id: 'ch_AAAA0001' }));
const newest = placed(seg('10:40', null));
const threeSteps = stepView([first, second, newest]);

describe('drawerNow', () => {
  it('is the hubs now as the step read answered it', () => {
    expect(drawerNow(threeSteps, 5)).toBe(Date.parse(at('11:00')));
  });

  it('falls back before the read answers', () => {
    expect(drawerNow(undefined, 5)).toBe(5);
  });
});

describe('placedOf', () => {
  it('is null before the read answers', () => {
    expect(placedOf(undefined, { chunkId: 'ch_AAAA0001', session: 'lease_10:00', at: null })).toBeNull();
  });

  it('is the newest step when the ref names none or no ref is given', () => {
    expect(placedOf(threeSteps, null)).toBe(newest);
    expect(placedOf(threeSteps, { chunkId: 'ch_AAAA0001', session: null, at: null })).toBe(newest);
  });

  it('is the newest step when the ref names another chunk', () => {
    expect(placedOf(threeSteps, { chunkId: 'ch_OTHER', session: 'lease_10:00', at: null })).toBe(newest);
  });

  it('is the step the ref names on the journey', () => {
    expect(placedOf(threeSteps, { chunkId: 'ch_AAAA0001', session: 'lease_10:00', at: null })).toBe(first);
  });

  it('is the step the ref names by the instant it began, for a step no session ran', () => {
    const hubSide = placed(seg('10:20', '10:30', { lease_id: null }));
    const view = stepView([first, hubSide, newest]);
    expect(placedOf(view, { chunkId: 'ch_AAAA0001', session: null, at: at('10:20') })).toBe(hubSide);
  });

  it('is the read step when the name is not on the journey', () => {
    expect(placedOf(threeSteps, { chunkId: 'ch_AAAA0001', session: 'lease_gone', at: null })).toBe(newest);
  });
});

describe('journeyBlocks', () => {
  it('is empty without a view, a current step or a journey', () => {
    expect(journeyBlocks(undefined, first, 0)).toEqual([]);
    expect(journeyBlocks(threeSteps, null, 0)).toEqual([]);
    expect(journeyBlocks(stepView([]), first, 0)).toEqual([]);
  });

  it('weights each step by its hold, running steps to now, and each gap by the queueing before it', () => {
    const blocks = journeyBlocks(threeSteps, second, Date.parse(at('10:50')));
    expect(blocks.map((b) => b.heldMs)).toEqual([10 * 60_000, 10 * 60_000, 10 * 60_000]);
    expect(blocks.map((b) => b.gapMs)).toEqual([0, 10 * 60_000, 10 * 60_000]);
  });

  it('gives an overlapping step no gap', () => {
    const view = stepView([placed(seg('10:00', '10:30')), placed(seg('10:10', '10:40')), placed(seg('10:45', '10:50'))]);
    expect(journeyBlocks(view, first, 0).map((b) => b.gapMs)).toEqual([0, 0, 5 * 60_000]);
  });

  it('keeps a step of no duration a sliver', () => {
    const view = stepView([placed(seg('10:00', '10:00'))]);
    expect(journeyBlocks(view, first, 0)[0].heldMs).toBe(1);
  });

  it('marks the current step, the running one and the failed one', () => {
    const view = stepView([placed(seg('10:00', '10:10', { failed: true })), newest]);
    const blocks = journeyBlocks(view, newest, Date.parse(at('10:50')));
    expect(blocks.map((b) => [b.current, b.live, b.failed])).toEqual([
      [false, false, true],
      [true, true, false],
    ]);
  });

  it('keys each block by its step ref and shades it by node', () => {
    const view = stepView([placed(seg('10:00', '10:10', { node_name: 'verify' }))]);
    expect(journeyBlocks(view, first, 0)[0]).toMatchObject({
      id: 'lease_10:00',
      key: { chunkId: 'ch_AAAA0001', session: 'lease_10:00', at: null },
      shade: 3,
      node: 'verify',
    });
  });

  it('marks where the environment changed', () => {
    const view = stepView([placed(seg('10:00', '10:10')), placed(seg('10:10', '10:20')), placed(seg('10:20', '10:30'), 'ws1', 'e2'), placed(seg('10:30', '10:40'), 'ws2', 'e2')]);
    expect(journeyBlocks(view, first, 0).map((b) => b.envChanged)).toEqual([false, false, true, true]);
  });

  it('titles a block with its node, hold, exit and where it ran', () => {
    const view = stepView([placed(seg('10:00', '10:10', { exit_choice: 'pass' })), placed(seg('10:10', '10:20'))]);
    const blocks = journeyBlocks(view, first, 0);
    expect(blocks[0].title).toBe('build · 10m → pass\nws1 / e1 · r1');
    expect(blocks[1].title).toBe('build · 10m\nws1 / e1 · r1');
  });
});

describe('neighbours', () => {
  it('names the steps either side of the current one', () => {
    expect(neighbours(threeSteps, second)).toEqual({ previous: first, next: newest });
  });

  it('has no previous at the start and no next at the end', () => {
    expect(neighbours(threeSteps, first)).toEqual({ previous: null, next: second });
    expect(neighbours(threeSteps, newest)).toEqual({ previous: second, next: null });
  });

  it('has neither when the current step is not on the journey, or nothing is read', () => {
    expect(neighbours(threeSteps, placed(seg('09:00', '09:10')))).toEqual({ previous: null, next: null });
    expect(neighbours(undefined, first)).toEqual({ previous: null, next: null });
    expect(neighbours(threeSteps, null)).toEqual({ previous: null, next: null });
  });
});

describe('stepModel', () => {
  it('prefers the spawn invocation', () => {
    const view = stepView([first], [inv({ harness_id: 'codex', model: 'gpt' }), inv({ kind: 'spawn', harness_id: 'claude', model: 'opus' })]);
    expect(stepModel(view)).toBe('claude · opus');
  });

  it('falls back to the first invocation', () => {
    const view = stepView([first], [inv({ harness_id: 'codex', model: 'gpt' }), inv({ harness_id: 'claude', model: 'opus' })]);
    expect(stepModel(view)).toBe('codex · gpt');
  });

  it('joins only what is known', () => {
    expect(stepModel(stepView([first], [inv({ model: 'opus' })]))).toBe('opus');
  });

  it('is null when neither is known, or there is no invocation', () => {
    expect(stepModel(stepView([first], [inv()]))).toBeNull();
    expect(stepModel(stepView([first]))).toBeNull();
    expect(stepModel(undefined)).toBeNull();
  });
});

describe('localStamp', () => {
  // Built from local components so the stamp reads the same in any timezone.
  const now = new Date(2026, 6, 1, 15, 0);
  const stamp = (y: number, m: number, d: number, h: number, min: number): string => localStamp(new Date(y, m, d, h, min).toISOString(), now);

  it('shows the time alone today', () => {
    expect(stamp(2026, 6, 1, 9, 5)).toBe('09:05');
  });

  it('prefixes yesterday', () => {
    expect(stamp(2026, 5, 30, 2, 59)).toBe('Yesterday 02:59');
  });

  it('shows the date and time for older instants', () => {
    expect(stamp(2026, 5, 20, 23, 7)).toBe('2026/06/20 23:07');
  });
});

describe('stepFacts', () => {
  const templates = { began: 'B', ended: 'E', spend: 'S' };

  it('is empty before the step is placed', () => {
    expect(stepFacts(null, 0, templates)).toEqual([]);
  });

  it('lists where, began, ended, held and spend', () => {
    expect(stepFacts(first, Date.parse(at('11:00')), templates)).toEqual([
      { label: 'Where', value: 'ws1 / e1' },
      { label: 'Began', template: 'B' },
      { label: 'Ended', template: 'E' },
      { label: 'Held', value: '10m' },
      { label: 'Spend', template: 'S' },
    ]);
  });

  it('says so far for a step still running', () => {
    const held = stepFacts(newest, Date.parse(at('10:50')), templates).find((f) => f.label === 'Held');
    expect(held).toEqual({ label: 'Held', value: '10m so far' });
  });
});

describe('chunkFor', () => {
  const detail = { chunk_id: 'ch_1', artifacts: [] } as unknown as ChunkDetail;

  it('is the detail once it answers for the chunk', () => {
    expect(chunkFor(detail, 'ch_1')).toBe(detail);
  });

  it('is null for a read still on another chunk, none, or no chunk', () => {
    expect(chunkFor(detail, 'ch_2')).toBeNull();
    expect(chunkFor(undefined, 'ch_1')).toBeNull();
    expect(chunkFor(detail, null)).toBeNull();
  });
});

describe('stepArtifactsOf', () => {
  type Artifact = ChunkDetail['artifacts'][number];
  const art = (key: string, o: Partial<Artifact> = {}): Artifact => ({ key, name: key, node_id: 'nd_build', epoch: 1, ...o }) as unknown as Artifact;
  const chunkWith = (...artifacts: Artifact[]): ChunkDetail => ({ chunk_id: 'ch_AAAA0001', title: 'kept', artifacts }) as unknown as ChunkDetail;
  const keys = (c: ChunkDetail | null): string[] => c!.artifacts.map((a) => a.key);

  it('is null without a chunk or the step read', () => {
    expect(stepArtifactsOf(null, [])).toBeNull();
    expect(stepArtifactsOf(chunkWith(), undefined)).toBeNull();
  });

  it('keeps the rest of the chunk and narrows its artifacts', () => {
    expect(stepArtifactsOf(chunkWith(art('a')), ['a'])).toMatchObject({ chunk_id: 'ch_AAAA0001', title: 'kept' });
  });

  it('keeps exactly the artifacts the step read names, whatever their node or epoch', () => {
    const chunk = chunkWith(art('mine'), art('also-mine', { node_id: 'nd_review', epoch: 7 }), art('not-mine'));
    expect(keys(stepArtifactsOf(chunk, ['mine', 'also-mine', 'gone']))).toEqual(['mine', 'also-mine']);
    expect(keys(stepArtifactsOf(chunk, []))).toEqual([]);
  });
});

describe('transcriptRefOf', () => {
  it('is the sessions transcript, fenced by its epoch', () => {
    expect(transcriptRefOf(seg('10:00', '10:10', { epoch: 3 }))).toEqual({
      chunkId: 'ch_AAAA0001',
      nodeId: 'nd_build',
      epoch: 3,
      live: false,
    });
  });

  it('is live while the step runs', () => {
    expect(transcriptRefOf(seg('10:00', null))?.live).toBe(true);
  });

  it('still reads a forced-entry takeover session, which has no session id', () => {
    expect(transcriptRefOf(seg('10:00', null, { lease_id: null, epoch: 2 }))?.epoch).toBe(2);
  });

  it('is null without a step, for a hub-side step, or without an epoch', () => {
    expect(transcriptRefOf(undefined)).toBeNull();
    expect(transcriptRefOf(seg('10:00', null, { executor: 'hub', lease_id: null, epoch: 4 }))).toBeNull();
    expect(transcriptRefOf(seg('10:00', null, { epoch: null }))).toBeNull();
  });
});

describe('stepIdentity', () => {
  it('names a step by its session', () => {
    expect(stepIdentity(seg('10:00', null))).toEqual({ label: 'lease_10:00', title: 'lease_10:00', hub: false });
  });

  it('names a hub-side step by its node, and badges it', () => {
    expect(stepIdentity(seg('10:00', null, { executor: 'hub', lease_id: null }))).toEqual({
      label: 'build',
      title: 'no session of its own — a hub-side step',
      hub: true,
    });
  });

  it('names a runner step with no known session by its node, saying why, with no hub badge', () => {
    expect(stepIdentity(seg('10:00', null, { lease_id: null, epoch: null }))).toEqual({
      label: 'build',
      title: "the hub never heard this step's session start",
      hub: false,
    });
  });

  it('names a forced-entry takeover session by its node, saying it took the chunk over', () => {
    expect(stepIdentity(seg('10:00', null, { lease_id: null, epoch: 3 }))).toEqual({
      label: 'build',
      title: 'a session that took the chunk over on a forced entry',
      hub: false,
    });
  });
});

describe('projectOf', () => {
  const project = (project_id: string, name: string, color: string): ProjectView => ({ project_id, name, color }) as ProjectView;
  const projects = [project('proj_1', 'Blizzard', '#abcdef'), project('proj_2', 'Teal', 'teal')];

  it('names the project with its raw colour', () => {
    expect(projectOf(true, projects, 'proj_1')).toEqual({ name: 'Blizzard', color: '#abcdef' });
  });

  it('hands a palette-named colour to the swatch raw, for it to convert once', () => {
    expect(projectOf(true, projects, 'proj_2')).toEqual({ name: 'Teal', color: 'teal' });
  });

  it('keeps the name and drops the colour under the gate', () => {
    expect(projectOf(false, projects, 'proj_2')).toEqual({ name: 'Teal', color: null });
  });

  it('is null for an unknown, missing or unread project', () => {
    expect(projectOf(true, projects, 'proj_9')).toBeNull();
    expect(projectOf(true, projects, null)).toBeNull();
    expect(projectOf(true, projects, undefined)).toBeNull();
    expect(projectOf(true, undefined, 'proj_1')).toBeNull();
  });
});

describe('drawerOrigin', () => {
  it('carries the layout and the step the drawer is on', () => {
    expect(drawerOrigin('tracks', first)).toEqual({ layout: 'tracks', from: 'lease_10:00', fromAt: null });
  });

  it('carries a step no session ran by the instant it began', () => {
    expect(drawerOrigin('tracks', placed(seg('10:00', '10:10', { lease_id: null })))).toEqual({
      layout: 'tracks',
      from: null,
      fromAt: at('10:00'),
    });
  });

  it('carries no step before one is placed', () => {
    expect(drawerOrigin(null, null)).toEqual({ layout: null, from: null, fromAt: null });
  });
});

describe('ownArtifactKeys', () => {
  it("names the step read's keys once it answers for this step", () => {
    expect(ownArtifactKeys({ artifact_keys: ['a', 'b'] }, false)).toEqual(['a', 'b']);
  });

  it("names none while the previous step's read is still held", () => {
    expect(ownArtifactKeys({ artifact_keys: ['a'] }, true)).toBeUndefined();
  });

  it('names none before the step is read', () => {
    expect(ownArtifactKeys(undefined, false)).toBeUndefined();
  });
});
