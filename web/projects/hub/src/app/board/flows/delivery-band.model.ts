import { compactRef, type FlowPlaceView, type FlowView, type PrView, type RepositorySummary, ageMs, formatAbsolute, formatAgo, formatWhen, type Tone, type WorkItemsView } from 'fleet';

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
  /** The open change an `awaiting-merge` chunk waits on, when one of its open PRs is on the flow's repository —
   * `null` otherwise, never a guessed link. */
  readonly change: LaneChange | null;
}

/** The forge-side change a parked chunk waits on. */
export interface LaneChange {
  readonly url: string;
  /** `owner/repo#12`. */
  readonly label: string;
}

/** What a lane joins on to link a parked chunk's change: each chunk's open PRs, and the tenant's repositories. */
export interface LaneChanges {
  readonly openPrs: ReadonlyMap<string, readonly PrView[]>;
  readonly repositories: readonly RepositorySummary[];
}

const NO_CHANGES: LaneChanges = { openPrs: new Map(), repositories: [] };

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

/** The flow repository's forge-side `owner/repo` — `null` while the repository list has not named it. */
function forgeRepo(flow: FlowView, repositories: readonly RepositorySummary[]): string | null {
  const repository = repositories.find((candidate) => candidate.repository_id === flow.repository_id);
  return repository ? `${repository.owner}/${repository.repo}` : null;
}

/** The open PR an `awaiting-merge` place waits on: the chunk's open PR on the flow's forge-side repository. */
function laneChange(place: FlowPlaceView, repo: string | null, openPrs: LaneChanges['openPrs']): LaneChange | null {
  if (place.state !== 'awaiting-merge' || repo === null) return null;
  const pr = openPrs.get(place.chunk_id)?.find((candidate) => candidate.repo === repo);
  return pr ? { url: pr.url, label: `${pr.repo}#${pr.number}` } : null;
}

function laneChunk(
  place: FlowPlaceView,
  workItems: ReadonlyMap<string, WorkItemsView>,
  now: Date,
  change: LaneChange | null = null,
): LaneChunk {
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
    change,
  };
}

/** One flow's lane: the chunk landing now, the rest of the line in arrival order, and the last verdict. */
export function deliveryLane(
  flow: FlowView,
  workItems: ReadonlyMap<string, WorkItemsView>,
  now: number,
  changes: LaneChanges = NO_CHANGES,
): DeliveryLane {
  const at = new Date(now);
  const repo = forgeRepo(flow, changes.repositories);
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
  const line = ordered.filter((place) => place.chunk_id !== landingId).map((place) => laneChunk(place, workItems, at, laneChange(place, repo, changes.openPrs)));
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
  changes: LaneChanges = NO_CHANGES,
): readonly DeliveryLane[] {
  return flows.map((flow) => deliveryLane(flow, workItems, now, changes));
}
