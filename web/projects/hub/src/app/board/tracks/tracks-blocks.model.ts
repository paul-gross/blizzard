import { compactRef, formatHeldFor } from 'fleet';

import type { EnvTimelineHost } from './env-timeline.model';
import {
  type EnvTimelineSegment,
  type StepKey,
  type StepRef,
  costLabel,
  hostLabel,
  nodeShade,
  segmentCost,
  stepEndMs,
  stepHeld,
  stepHeldMs,
  stepRefKey,
  stepRefOfSegment,
} from './step.model';

/*
 * The blocks on one environment's row. Zoomed in, each node step is its own block; zoomed out, steps too narrow to see
 * fold into their chunk's neighbouring narrow steps, so a fortnight of a busy fleet is a few hundred blocks, not
 * thousands of slivers — vis lays every block out on its own, and the slivers were what made a wide window crawl.
 */

/** A block's id: its row, then the step it draws — unique even when one claim held two environments. Opaque: the
 * step a block opens rides beside it as {@link TrackItem.step}, never parsed back out of it. */
function itemId(groupId: string, segment: EnvTimelineSegment): string {
  return `${groupId}#${segment.chunk_id}@${segment.started_at}~${stepRefKey(stepRefOfSegment(segment))}`;
}

/** One block on an environment's row — a node step, or a fold of a chunk's narrow steps, from its start to its end (or now). */
export interface TrackItem {
  readonly id: string;
  /** The step a pick on the block opens — for a fold, its longest step. */
  readonly step: StepRef;
  readonly group: string;
  readonly start: number;
  readonly end: number;
  /** The chunk's compact ref on the widest block of its run on this row; empty on the rest. */
  readonly content: string;
  readonly className: string;
  /** Inline style — the project's hue when blocks are coloured by project. */
  readonly style?: string;
  /** The hover tooltip, plain text, one fact per line. */
  readonly title: string;
}

/** How many runner hues the theme carries (tk-r0…); more runners than that share them round-robin. */
export const RUNNER_HUES = 4;

function where(segment: EnvTimelineSegment, host: EnvTimelineHost): string {
  return `${hostLabel(host)} · ${segment.runner_name}`;
}

function chunkLine(segment: EnvTimelineSegment): string {
  return `${compactRef(segment.chunk_id)}${segment.title ? ` ${segment.title}` : ''}`;
}

/** A step's hover: its chunk, node, where it ran, how long and for what, and how it left. */
export function stepTooltip(segment: EnvTimelineSegment, host: EnvTimelineHost, nowMs: number): string {
  const lines = [
    chunkLine(segment),
    segment.graph_name === null ? segment.node_name : `${segment.graph_name} › ${segment.node_name}`,
    where(segment, host),
    `${stepHeld(segment, nowMs)}${segment.ended_at === null ? ' so far' : ''} · ${segmentCost(segment)}`,
  ];
  if (segment.exit_choice !== null) lines.push(`exit: ${segment.exit_choice}`);
  if (segment.escalation !== null) lines.push(`escalated: ${segment.escalation}`);
  else if (segment.failed) lines.push('failed');
  return lines.join('\n');
}

function foldTooltip(steps: readonly EnvTimelineSegment[], host: EnvTimelineHost, nowMs: number): string {
  const first = steps[0];
  const last = steps[steps.length - 1];
  const running = last.ended_at === null;
  const cost = costLabel(
    steps.reduce((sum, s) => sum + s.cost_usd, 0),
    steps.some((s) => s.cost_estimated),
    running,
  );
  const failed = steps.filter((s) => s.failed).length;
  const lines = [
    chunkLine(first),
    `${steps.length} steps · ${first.node_name} → ${last.node_name}`,
    where(last, host),
    `${formatHeldFor(stepEndMs(last, nowMs) - Date.parse(first.started_at))}${running ? ' so far' : ''} · ${cost}`,
  ];
  if (last.escalation !== null) lines.push(`escalated: ${last.escalation}`);
  if (failed > 0) lines.push(`${failed} failed`);
  lines.push('zoom in for each step');
  return lines.join('\n');
}

/** What colours a row's blocks: each runner's hue, or each project's. */
export interface BlockPaint {
  readonly runnerHue: ReadonlyMap<string, number> | null;
  readonly projectHues: ReadonlyMap<string, string> | null;
}

/** A row's blocks, as {@link envBlocks} lays them out. */
export interface BlockRow {
  readonly groupId: string;
  readonly host: EnvTimelineHost;
  readonly segments: readonly EnvTimelineSegment[];
}

/** One block to draw: a lone step, or a fold of consecutive narrow steps of one chunk. */
interface Block {
  readonly steps: EnvTimelineSegment[];
  readonly narrow: boolean;
}

/** A chunk's consecutive steps on a row, in blocks: each wide step alone, each stretch of narrow steps folded. */
function runBlocks(run: readonly EnvTimelineSegment[], nowMs: number, minBlockMs: number): Block[] {
  const blocks: Block[] = [];
  for (const step of run) {
    const narrow = stepHeldMs(step, nowMs) < minBlockMs;
    const last = blocks.at(-1);
    if (narrow && last?.narrow) last.steps.push(step);
    else blocks.push({ steps: [step], narrow });
  }
  return blocks;
}

function blockSpan(block: Block, nowMs: number): number {
  return stepEndMs(block.steps[block.steps.length - 1], nowMs) - Date.parse(block.steps[0].started_at);
}

function blockClass(block: Block, lead: EnvTimelineSegment, paint: BlockPaint, selected: StepKey | null): string {
  const steps = block.steps;
  const last = steps[steps.length - 1];
  const classes = ['tk-seg', `tk-n${nodeShade(lead.node_name)}`];
  if (paint.runnerHue !== null) classes.push(`tk-r${paint.runnerHue.get(lead.runner_name) ?? 0}`);
  if (steps.length > 1) classes.push('tk-fold');
  if (steps.some((s) => s.failed)) classes.push('tk-failed');
  // Only an escalation still open marks the block; a superseded one is history, left to the hover.
  if (last.escalation_open) classes.push('tk-escalated');
  if (last.ended_at === null) classes.push('tk-live');
  if (selected !== null && lead.chunk_id === selected.chunkId)
    classes.push(steps.some((s) => s.started_at === selected.startedAt) ? 'tk-selected' : 'tk-same-chunk');
  return classes.join(' ');
}

/**
 * The blocks of one environment's row. A step narrower than `minBlockMs` folds with the narrow steps beside it of the
 * same chunk; a fold wears its longest step's shade and opens that step when picked. A chunk's ref rides the widest
 * block of each consecutive run of it on the row — the first is often a sliver.
 */
export function envBlocks(row: BlockRow, nowMs: number, minBlockMs: number, paint: BlockPaint, selected: StepKey | null): TrackItem[] {
  const items: TrackItem[] = [];
  const segments = row.segments;
  for (let i = 0; i < segments.length; ) {
    let j = i + 1;
    while (j < segments.length && segments[j].chunk_id === segments[i].chunk_id) j++;
    const blocks = runBlocks(segments.slice(i, j), nowMs, minBlockMs);
    i = j;
    let widest = 0;
    blocks.forEach((b, k) => {
      if (blockSpan(b, nowMs) > blockSpan(blocks[widest], nowMs)) widest = k;
    });
    blocks.forEach((block, k) => {
      const steps = block.steps;
      const lead = steps.reduce((a, b) => (stepHeldMs(b, nowMs) > stepHeldMs(a, nowMs) ? b : a));
      const start = Date.parse(steps[0].started_at);
      items.push({
        id: itemId(row.groupId, lead),
        step: stepRefOfSegment(lead),
        group: row.groupId,
        start,
        end: Math.max(stepEndMs(steps[steps.length - 1], nowMs), start + 1),
        content: k === widest ? compactRef(lead.chunk_id) : '',
        className: blockClass(block, lead, paint, selected),
        ...(paint.projectHues !== null
          ? { style: `--tk-hue: ${paint.projectHues.get(lead.project_id ?? '') ?? 'transparent'}` }
          : {}),
        title: steps.length === 1 ? stepTooltip(lead, row.host, nowMs) : foldTooltip(steps, row.host, nowMs),
      });
    });
  }
  return items;
}
