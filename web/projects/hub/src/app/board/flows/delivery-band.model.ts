import { compactRef, type FlowPlaceView, type FlowView, ageMs, formatAbsolute, formatAgo, formatWhen, type Tone, type WorkItemsView } from 'fleet';

import { placeStateLabel, placeStateTone } from './flow-place.model';

/** One chunk as a lane shows it: its short id, its title once read, and its state on the flow. */
export interface LaneChunk {
  readonly chunkId: string;
  readonly shortId: string;
  /** The chunk's first work item's title — `null` until the board's work-items read has it. */
  readonly title: string | null;
  readonly state: string;
  readonly stateLabel: string;
  readonly tone: Tone;
  /** When a polling chunk's next poll is due, as a short stamp — `null` for every other state. */
  readonly pollAt: string | null;
  readonly pollAtTitle: string | null;
}

/** The flow's last verdict, and how long ago it came. */
export interface LaneVerdict {
  readonly outcome: string;
  readonly ago: string;
  readonly agoTitle: string;
}

/** One flow as the Delivery band shows it. */
export interface DeliveryLane {
  readonly flowId: string;
  /** The lane's title: its repository's name and its branch, `blizzard · master`. */
  readonly title: string;
  /** The chunk whose turn is open on the flow, or `null`. */
  readonly landing: LaneChunk | null;
  /** The chunks in line behind it, in arrival order. */
  readonly line: readonly LaneChunk[];
  readonly verdict: LaneVerdict | null;
  /** Whether the lane folds to one line of its last verdict — nothing landing and no one in line. */
  readonly collapsed: boolean;
}

/** A flow's lane title: its repository's name, else its slug, else its resource, then its branch. */
export function laneTitle(flow: FlowView): string {
  return `${flow.repository_name ?? flow.repository_slug ?? flow.resource} · ${flow.branch}`;
}

function laneChunk(place: FlowPlaceView, workItems: ReadonlyMap<string, WorkItemsView>, now: Date): LaneChunk {
  const polling = place.state === 'polling' && place.next_poll_at !== null;
  return {
    chunkId: place.chunk_id,
    shortId: compactRef(place.chunk_id),
    title: workItems.get(place.chunk_id)?.items.find((item) => item.title)?.title ?? null,
    state: place.state,
    stateLabel: placeStateLabel(place.state),
    tone: placeStateTone(place.state),
    pollAt: polling ? formatWhen(place.next_poll_at ?? '', now) : null,
    pollAtTitle: polling ? formatAbsolute(place.next_poll_at) : null,
  };
}

/** One flow's lane: the chunk landing now, the rest of the line in arrival order, and the last verdict. */
export function deliveryLane(flow: FlowView, workItems: ReadonlyMap<string, WorkItemsView>, now: number): DeliveryLane {
  const at = new Date(now);
  const ordered = [...flow.line].sort((a, b) => a.arrival - b.arrival);
  const landingId = flow.turn?.chunk_id ?? ordered.find((place) => place.state === 'landing')?.chunk_id ?? null;
  const landingPlace = ordered.find((place) => place.chunk_id === landingId);
  const landing =
    landingId === null
      ? null
      : laneChunk(
          landingPlace ?? { chunk_id: landingId, arrival: 0, next_poll_at: null, position: 1, state: 'landing', submission_id: '' },
          workItems,
          at,
        );
  const line = ordered.filter((place) => place.chunk_id !== landingId).map((place) => laneChunk(place, workItems, at));
  return { flowId: flow.flow_id, title: laneTitle(flow), landing, line, verdict: laneVerdict(flow, now), collapsed: landing === null && line.length === 0 };
}

/** A flow's last verdict and how long ago it came — `—` for an instant too far ahead of the clock to trust. */
function laneVerdict(flow: FlowView, now: number): LaneVerdict | null {
  const verdict = flow.last_verdict;
  if (!verdict) return null;
  const age = ageMs(verdict.at, now);
  return { outcome: verdict.outcome ?? verdict.kind, ago: age === null ? '—' : formatAgo(age), agoTitle: formatAbsolute(verdict.at) };
}

/** Every active flow's lane, in the order the hub lists them. A flow the read omits — past the board window with
 * nothing in line — has no lane, and no active flow leaves the band empty. */
export function deliveryLanes(
  flows: readonly FlowView[],
  workItems: ReadonlyMap<string, WorkItemsView>,
  now: number,
): readonly DeliveryLane[] {
  return flows.map((flow) => deliveryLane(flow, workItems, now));
}
