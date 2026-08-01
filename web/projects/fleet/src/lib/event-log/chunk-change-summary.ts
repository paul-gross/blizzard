import { compactRef } from '../compact-ref';
import type { LoggedEvent } from '../sse/fleet-live';

/** A `chunk-changed` frame shaped into the Event log's two-line block (issue #212). */
export interface ChunkChangeSummary {
  /** Line 1 — the chunk shortname and its transition, e.g. `C-1RJ1 review → failed → build`. */
  readonly transition: string;
  /** Line 2 — the runner shortname, e.g. `runner-local`. Omitted on an unclaimed transition. */
  readonly runner?: string;
}

/**
 * Shape a `chunk-changed` frame into the block row's two lines.
 *
 * `transition` joins the chunk ref, the previous node, the status, and the next node
 * with the panel's existing `→` vocabulary — each absent segment (and its adjacent
 * arrow) is dropped rather than rendered as placeholder junk (issue #212 AC 5, widened
 * to `status` by issue #213 Phase 4 — a backfilled row can structurally carry no
 * status yet, `hub/domain/work.py`'s `ActivityRow`), so a frame carrying neither node
 * degrades to exactly today's `C-1NWW → running`, and a frame carrying a node but no
 * status renders e.g. `C-1RJ1 review → build` rather than `C-1RJ1 review → — →
 * build`. `runner` is the compact runner ref when the frame names one, else omitted —
 * an unclaimed transition (e.g. a promote or a stop past the point the route
 * released) renders no runner line at all rather than an empty one.
 *
 * `graph_id` is deliberately never read here — it rides the wire (AC 4) but is not
 * part of the rendered row.
 */
export function summarizeChunkChange(data: LoggedEvent['data']): ChunkChangeSummary {
  const segments: string[] = [compactRef(data.chunk_id ?? '—')];
  if (data.prev_node) segments.push(data.prev_node);
  if (data.status) segments.push('→', data.status);
  if (data.node) segments.push('→', data.node);
  const summary: ChunkChangeSummary = { transition: segments.join(' ') };
  return data.runner_id ? { ...summary, runner: compactRef(data.runner_id) } : summary;
}
