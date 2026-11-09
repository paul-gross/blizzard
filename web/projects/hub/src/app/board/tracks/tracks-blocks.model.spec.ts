import { describe, expect, it } from 'vitest';

import type { EnvTimelineHost } from './env-timeline.model';
import { type BlockPaint, type BlockRow, envBlocks, stepTooltip } from './tracks-blocks.model';
import type { EnvTimelineSegment } from './step.model';

const MIN = 60_000;
const BASE = Date.parse('2026-07-01T10:00:00Z');
const NOW = BASE + 120 * MIN;
const iso = (offsetSec: number): string => new Date(BASE + offsetSec * 1000).toISOString();

/** A step from `startSec` to `endSec` after 10:00 (`null` while running). */
const seg = (startSec: number, endSec: number | null, overrides: Partial<EnvTimelineSegment> = {}): EnvTimelineSegment => ({
  chunk_id: 'ch_AAAA0001',
  cost_estimated: false,
  cost_usd: 0,
  ended_at: endSec === null ? null : iso(endSec),
  epoch: 1,
  escalation: null,
  escalation_open: false,
  executor: 'runner',
  exit_choice: null,
  failed: false,
  graph_id: null,
  graph_name: 'delivery',
  lease_id: `lease_${startSec}`,
  node_id: 'nd_build',
  node_name: 'build',
  project_id: null,
  runner_id: 'rn_1',
  runner_name: 'r1',
  started_at: iso(startSec),
  title: null,
  ...overrides,
});

const host: EnvTimelineHost = { host_id: 'hs_1', host_name: 'xps', runner_names: ['r1'], workspaces: [] };
const row = (segments: EnvTimelineSegment[]): BlockRow => ({ groupId: 'e:hs_1:ws1/e1', host, segments });
const plain: BlockPaint = { runnerHue: null, projectHues: null };
const MIN_BLOCK = 60_000;

describe('envBlocks level of detail', () => {
  it('draws each wide step as its own block', () => {
    const items = envBlocks(row([seg(0, 600, { node_name: 'plan' }), seg(600, 1200)]), NOW, MIN_BLOCK, plain, null);
    expect(items.map((i) => [i.start, i.end])).toEqual([
      [BASE, BASE + 600_000],
      [BASE + 600_000, BASE + 1_200_000],
    ]);
    expect(items.map((i) => i.group)).toEqual(['e:hs_1:ws1/e1', 'e:hs_1:ws1/e1']);
  });

  it('folds consecutive narrow steps of a chunk into one block spanning them', () => {
    const items = envBlocks(row([seg(0, 10), seg(10, 20), seg(20, 30)]), NOW, MIN_BLOCK, plain, null);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ start: BASE, end: BASE + 30_000 });
    expect(items[0].className).toContain('tk-fold');
  });

  it('never folds a wide step into the narrow ones beside it', () => {
    const items = envBlocks(row([seg(0, 10), seg(10, 610), seg(610, 620), seg(620, 630)]), NOW, MIN_BLOCK, plain, null);
    expect(items.map((i) => i.className.includes('tk-fold'))).toEqual([false, false, true]);
    expect(items.map((i) => i.start)).toEqual([BASE, BASE + 10_000, BASE + 610_000]);
  });

  it('does not fold narrow steps of different chunks', () => {
    const items = envBlocks(row([seg(0, 10), seg(10, 20, { chunk_id: 'ch_BBBB0002' })]), NOW, MIN_BLOCK, plain, null);
    expect(items).toHaveLength(2);
    expect(items.every((i) => !i.className.includes('tk-fold'))).toBe(true);
  });

  it('draws every step alone when none is narrower than the minimum', () => {
    expect(envBlocks(row([seg(0, 10), seg(10, 20)]), NOW, 0, plain, null)).toHaveLength(2);
  });

  it('takes a folds id and shade from its longest step', () => {
    const steps = [seg(0, 5, { node_name: 'plan' }), seg(5, 35, { node_name: 'deliver' }), seg(35, 40, { node_name: 'review' })];
    const [fold] = envBlocks(row(steps), NOW, MIN_BLOCK, plain, null);
    expect(fold.className).toContain('tk-n7');
    expect(fold.step).toEqual({ chunkId: 'ch_AAAA0001', session: 'lease_5', at: null });
  });

  it('counts a running step as held to now', () => {
    const [item] = envBlocks(row([seg(7000, null)]), NOW, MIN_BLOCK, plain, null);
    expect(item.end).toBe(NOW);
    expect(item.className).toContain('tk-live');
  });

  it('keeps a block at least one millisecond wide', () => {
    const [item] = envBlocks(row([seg(5, 5)]), NOW, 0, plain, null);
    expect(item.end).toBe(item.start + 1);
  });
});

describe('envBlocks chunk ref', () => {
  it('rides the widest block of a run, not the sliver before it', () => {
    const items = envBlocks(row([seg(0, 10), seg(10, 700)]), NOW, MIN_BLOCK, plain, null);
    expect(items.map((i) => i.content)).toEqual(['', 'C-0001']);
  });

  it('goes to the first of equally wide blocks', () => {
    const items = envBlocks(row([seg(0, 600), seg(600, 1200)]), NOW, MIN_BLOCK, plain, null);
    expect(items.map((i) => i.content)).toEqual(['C-0001', '']);
  });

  it('is repeated on each separate run of the same chunk', () => {
    const items = envBlocks(
      row([seg(0, 600), seg(600, 1200, { chunk_id: 'ch_BBBB0002' }), seg(1200, 1800)]),
      NOW,
      MIN_BLOCK,
      plain,
      null,
    );
    expect(items.map((i) => i.content)).toEqual(['C-0001', 'C-0002', 'C-0001']);
  });
});

describe('envBlocks class names', () => {
  it('composes the segment and node shade classes', () => {
    const [item] = envBlocks(row([seg(0, 600, { node_name: 'verify' })]), NOW, MIN_BLOCK, plain, null);
    expect(item.className).toBe('tk-seg tk-n3');
  });

  it('adds the runners hue class only when blocks wear runner hues', () => {
    const paint: BlockPaint = { runnerHue: new Map([['r1', 2]]), projectHues: null };
    expect(envBlocks(row([seg(0, 600)]), NOW, MIN_BLOCK, paint, null)[0].className).toBe('tk-seg tk-n2 tk-r2');
    const unknown: BlockPaint = { runnerHue: new Map(), projectHues: null };
    expect(envBlocks(row([seg(0, 600)]), NOW, MIN_BLOCK, unknown, null)[0].className).toBe('tk-seg tk-n2 tk-r0');
  });

  it('marks a failed step', () => {
    expect(envBlocks(row([seg(0, 600, { failed: true })]), NOW, MIN_BLOCK, plain, null)[0].className).toContain('tk-failed');
  });

  it('marks a fold failed when any of its steps failed', () => {
    const [fold] = envBlocks(row([seg(0, 10), seg(10, 20, { failed: true }), seg(20, 30)]), NOW, MIN_BLOCK, plain, null);
    expect(fold.className).toContain('tk-failed');
  });

  it('marks an open escalation by the last step of the block', () => {
    const escalated = envBlocks(row([seg(0, 600, { escalation: 'budget', escalation_open: true })]), NOW, MIN_BLOCK, plain, null)[0];
    expect(escalated.className).toContain('tk-escalated');
    const earlier = envBlocks(row([seg(0, 10, { escalation: 'budget', escalation_open: true }), seg(10, 20)]), NOW, MIN_BLOCK, plain, null)[0];
    expect(earlier.className).not.toContain('tk-escalated');
  });

  it('leaves a superseded escalation unmarked on the block, but keeps it in the hover', () => {
    const [block] = envBlocks(row([seg(0, 600, { escalation: 'budget', escalation_open: false })]), NOW, MIN_BLOCK, plain, null);
    expect(block.className).not.toContain('tk-escalated');
    expect(block.title).toContain('escalated: budget');
  });

  it('marks the selected step, and the rest of its chunk', () => {
    const selected = { chunkId: 'ch_AAAA0001', startedAt: iso(600) };
    const items = envBlocks(
      row([seg(0, 600), seg(600, 1200), seg(1200, 1800, { chunk_id: 'ch_BBBB0002' })]),
      NOW,
      MIN_BLOCK,
      plain,
      selected,
    );
    expect(items[0].className).toContain('tk-same-chunk');
    expect(items[0].className).not.toContain('tk-selected');
    expect(items[1].className).toContain('tk-selected');
    expect(items[2].className).not.toMatch(/tk-selected|tk-same-chunk/);
  });

  it('marks a fold selected when the selected step is inside it', () => {
    const [fold] = envBlocks(row([seg(0, 10), seg(10, 20)]), NOW, MIN_BLOCK, plain, { chunkId: 'ch_AAAA0001', startedAt: iso(10) });
    expect(fold.className).toContain('tk-selected');
  });

  it('marks a chunk selected without a step as its whole self', () => {
    const [item] = envBlocks(row([seg(0, 600)]), NOW, MIN_BLOCK, plain, { chunkId: 'ch_AAAA0001', startedAt: '' });
    expect(item.className).toContain('tk-same-chunk');
  });
});

describe('envBlocks project hue', () => {
  it('styles a block with its projects hue', () => {
    const paint: BlockPaint = { runnerHue: null, projectHues: new Map([['proj_1', '#ff0000']]) };
    const [item] = envBlocks(row([seg(0, 600, { project_id: 'proj_1' })]), NOW, MIN_BLOCK, paint, null);
    expect(item.style).toBe('--tk-hue: #ff0000');
  });

  it('leaves a block with no known project transparent', () => {
    const paint: BlockPaint = { runnerHue: null, projectHues: new Map() };
    const items = envBlocks(row([seg(0, 600, { project_id: 'proj_x' }), seg(600, 1200)]), NOW, MIN_BLOCK, paint, null);
    expect(items.map((i) => i.style)).toEqual(['--tk-hue: transparent', '--tk-hue: transparent']);
  });

  it('carries no style when blocks wear runner hues', () => {
    expect(envBlocks(row([seg(0, 600)]), NOW, MIN_BLOCK, plain, null)[0]).not.toHaveProperty('style');
  });
});

describe('tooltips', () => {
  it('writes a step hover one fact per line', () => {
    const s = seg(0, 600, { title: 'Fix it', cost_usd: 1.5, exit_choice: 'pass' });
    expect(stepTooltip(s, host, NOW)).toBe(['C-0001 Fix it', 'delivery › build', 'xps · r1', '10m · $1.50', 'exit: pass'].join('\n'));
  });

  it('says so far for a running step and shows escalation instead of failure', () => {
    const s = seg(0, null, { escalation: 'budget', failed: true });
    expect(stepTooltip(s, host, NOW)).toBe(
      ['C-0001', 'delivery › build', 'xps · r1', '2h00m so far · no usage yet', 'escalated: budget'].join('\n'),
    );
  });

  it('names the node alone when its graph is unknown', () => {
    expect(stepTooltip(seg(0, 600, { graph_name: null }), host, NOW).split('\n')[1]).toBe('build');
  });

  it('notes a failed step', () => {
    expect(stepTooltip(seg(0, 600, { failed: true }), host, NOW).split('\n').at(-1)).toBe('failed');
  });

  it('names the host by its compact id when it has no name, or no host at all', () => {
    expect(stepTooltip(seg(0, 600), { ...host, host_name: null }, NOW)).toContain('H-');
    expect(stepTooltip(seg(0, 600), { ...host, host_id: null, host_name: null }, NOW)).toContain('no host · r1');
  });

  it('gives a fold its own hover summarising the steps', () => {
    const steps = [
      seg(0, 10, { node_name: 'plan', cost_usd: 0.5 }),
      seg(10, 20, { node_name: 'build', failed: true, cost_usd: 0.25 }),
      seg(20, 30, { node_name: 'verify', cost_usd: 0.25 }),
    ];
    const [fold] = envBlocks(row(steps), NOW, MIN_BLOCK, plain, null);
    expect(fold.title).toBe(
      ['C-0001', '3 steps · plan → verify', 'xps · r1', '30s · $1.00', '1 failed', 'zoom in for each step'].join('\n'),
    );
  });

  it('shows escalation of the last step and so far for a running fold', () => {
    const steps = [seg(7000, 7010), seg(7010, null, { escalation: 'budget' })];
    const [fold] = envBlocks(row(steps), BASE + 7020 * 1000, MIN_BLOCK, plain, null);
    expect(fold.title).toContain('20s so far');
    expect(fold.title).toContain('escalated: budget');
  });
});

describe('the step a block opens', () => {
  it('rides beside the block, naming a step by its session', () => {
    const [item] = envBlocks(row([seg(0, 600)]), NOW, MIN_BLOCK, plain, null);
    expect(item.step).toEqual({ chunkId: 'ch_AAAA0001', session: 'lease_0', at: null });
  });

  it('names a step no session ran by the instant it began', () => {
    const [item] = envBlocks(row([seg(0, 600, { lease_id: null })]), NOW, MIN_BLOCK, plain, null);
    expect(item.step).toEqual({ chunkId: 'ch_AAAA0001', session: null, at: iso(0) });
  });

  it('mints a distinct id for each block, whatever the step names', () => {
    const items = envBlocks(row([seg(0, 600), seg(600, 1200, { lease_id: null })]), NOW, MIN_BLOCK, plain, null);
    expect(new Set(items.map((i) => i.id)).size).toBe(2);
  });
});
