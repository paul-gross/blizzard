import type { GraphNodeView } from '../api/hub';
import { producesNames, sessionLabel } from './graph-node';

/**
 * Sizes a node's box to its *measured* text (issue #157: "measured text, not
 * char-count estimation") — split out of `graph-layout.ts` as the pure
 * text-measurement/box-sizing seam, kept under the `web:lint` line cap.
 */

/** A node box's height with a single (or no) meta line — the baseline every extra
 * wrapped meta line adds {@link META_LINE_HEIGHT} to. */
const BASE_NODE_HEIGHT = 60;
/** Vertical advance between two wrapped meta lines, matching `.node-meta`'s 11px
 * type in `graph-diagram.ts`. Exported so the component places line *i* at the same
 * step the height derivation reserved for it. */
export const META_LINE_HEIGHT = 15;
/** The y offset of the first meta line's baseline within a node box — the component's
 * `node.y + META_FIRST_LINE_Y`, from which further lines step by {@link META_LINE_HEIGHT}. */
export const META_FIRST_LINE_Y = 44;

const NAME_PAD_L = 14;
const NAME_GAP = 10;
const BADGE_PAD_X = 6;
const BADGE_GAP_R = 8;
const META_PAD_X = 14;
const MIN_NODE_WIDTH = 150;
/** The width past which a node's meta line wraps onto further lines instead of
 * widening the box further — without it a long `produces:` list alone dictates an
 * absurdly wide box (issue #157). It bounds *wrapping*, not the box: the name row
 * never wraps, and a single unsplittable meta segment wider than this still widens
 * the box rather than being clipped. */
const MAX_NODE_WIDTH = 420;
const LABEL_PAD_X = 7;
/** An edge label's fixed box height — the layout core positions it alongside
 * {@link labelBoxWidth}'s measured width. */
export const LABEL_HEIGHT = 20;
/** The separator drawn between two meta segments sharing a line. */
const META_SEPARATOR = ' · ';

/** The kinds of text the diagram sizes boxes around — a node's id/name, its
 * executor badge, its meta line (session/judged-by/mode/retries/produces), and a
 * choice-id edge label. Distinct kinds so a real measurer can pick the matching
 * font/weight/size without the pure layout module knowing anything about fonts. */
export type TextKind = 'name' | 'badge' | 'meta' | 'label';

/** Measures one string's rendered pixel width for the given {@link TextKind}. The
 * production measurer (in `graph-diagram.ts`) uses canvas `measureText`; tests
 * inject a deterministic stub — this seam is what keeps `layoutGraph` unit-testable
 * without a DOM (per the spike: "measured text, not char-count estimation", kept
 * out of this DOM-free module). */
export type TextMeasurer = (text: string, kind: TextKind) => number;

/** The meta line's segments, in render order — the atoms wrapping packs into lines.
 * `session` renders in its authored form (`resume:<node>` for a targeted resume) via
 * the shared {@link sessionLabel}, so the diagram and the detail table agree. */
function nodeMetaSegments(node: GraphNodeView): string[] {
  const meta: string[] = [];
  if (node.session) meta.push(sessionLabel(node));
  if (node.judged_by === 'human') meta.push('judged: human');
  if (node.mode) meta.push(node.mode);
  if (node.retries_max !== undefined && node.retries_max !== null) meta.push(`retries ${node.retries_max}`);
  const produces = producesNames(node);
  if (produces && produces.length > 0) {
    meta.push(`→ ${produces.join(', ')}`);
  }
  return meta;
}

/** Greedily packs meta segments into lines no wider than `maxTextWidth`, breaking only
 * at the ` · ` separator. A single segment wider than the budget takes a line of its
 * own and overflows it — segments are the smallest unit here, never split mid-token. */
function wrapMetaSegments(segments: readonly string[], measure: TextMeasurer, maxTextWidth: number): string[] {
  const lines: string[] = [];
  let current = '';
  for (const segment of segments) {
    if (current === '') {
      current = segment;
      continue;
    }
    const candidate = current + META_SEPARATOR + segment;
    if (measure(candidate, 'meta') <= maxTextWidth) current = candidate;
    else {
      lines.push(current);
      current = segment;
    }
  }
  if (current !== '') lines.push(current);
  return lines;
}

export interface NodeBox {
  readonly metaLines: readonly string[];
  readonly width: number;
  readonly height: number;
}

/** Sizes one node's box to its *measured* text: the name row (never wrapped) sets a
 * floor, and the meta line wraps at {@link MAX_NODE_WIDTH} with the box growing
 * downward by {@link META_LINE_HEIGHT} per extra line (issue #157). */
export function nodeBox(node: GraphNodeView, measure: TextMeasurer): NodeBox {
  const badgeWidth = measure(node.executor.toUpperCase(), 'badge') + BADGE_PAD_X * 2;
  const nameRow = NAME_PAD_L + measure(node.name, 'name') + NAME_GAP + badgeWidth + BADGE_GAP_R;
  const metaLines = wrapMetaSegments(nodeMetaSegments(node), measure, MAX_NODE_WIDTH - META_PAD_X * 2);
  const metaRow = metaLines.reduce((max, line) => Math.max(max, META_PAD_X * 2 + measure(line, 'meta')), 0);
  return {
    metaLines,
    width: Math.max(MIN_NODE_WIDTH, Math.ceil(nameRow), Math.ceil(metaRow)),
    height: BASE_NODE_HEIGHT + Math.max(0, metaLines.length - 1) * META_LINE_HEIGHT,
  };
}

export function labelBoxWidth(text: string, measure: TextMeasurer): number {
  return Math.ceil(measure(text, 'label') + LABEL_PAD_X * 2);
}

const MIGRATION_HEIGHT = 32;
const MIGRATION_PAD_X = 14;
const MIGRATION_MIN_WIDTH = 90;

export interface MigrationBox {
  readonly width: number;
  readonly height: number;
}

/** Sizes a migration sink's pill to its measured target-graph name — the same
 * "measured text, not char-count estimate" contract as {@link nodeBox}, sized with
 * the `name` text kind since the pill's text plays the same visual role a node's
 * name row does (it is, after all, the label the diagram gives that exit). */
export function migrationBox(targetGraph: string, measure: TextMeasurer): MigrationBox {
  return {
    width: Math.max(MIGRATION_MIN_WIDTH, Math.ceil(MIGRATION_PAD_X * 2 + measure(targetGraph, 'name'))),
    height: MIGRATION_HEIGHT,
  };
}
