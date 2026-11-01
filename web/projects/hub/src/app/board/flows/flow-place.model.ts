import { type ChunkFlowPlaceView, type hubApi, openLookup, type Tone } from 'fleet';

/** How a place's state reads, and the tone it is drawn in. A state this build does not know reads raw, in the idle
 * tone. */
interface PlaceStateLook {
  readonly label: string;
  readonly tone: Tone;
}

const PLACE_STATES = openLookup<hubApi.FlowPlaceState, PlaceStateLook>(
  {
    landing: { label: 'landing', tone: 'running' },
    next: { label: 'next', tone: 'spawning' },
    waiting: { label: 'waiting', tone: 'waiting' },
    polling: { label: 'polling', tone: 'waiting' },
    'awaiting-merge': { label: 'awaiting merge', tone: 'idle' },
    'passed-over': { label: 'passed over', tone: 'idle' },
  },
  (raw) => ({ label: raw, tone: 'idle' }),
);

/** A place state's label — the state raw when this build does not know it. */
export function placeStateLabel(state: string): string {
  return PLACE_STATES(state).label;
}

/** A place state's tone — `idle` when this build does not know it. */
export function placeStateTone(state: string): Tone {
  return PLACE_STATES(state).tone;
}

/** An English ordinal: `1st`, `2nd`, `3rd`, `4th`, `11th`, `22nd`. */
export function ordinal(n: number): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  const suffix = ['th', 'st', 'nd', 'rd'][n % 10] ?? 'th';
  return `${n}${suffix}`;
}

/** The branch a flow lands on, named by its repository: `blizzard/master`. A repository the hub names by neither name
 * nor slug falls back to the flow's resource. */
export function flowPath(flow: {
  readonly repository_name: string | null;
  readonly repository_slug: string | null;
  readonly resource: string;
  readonly branch: string;
}): string {
  const repo = flow.repository_name ?? flow.repository_slug;
  return repo === null ? flow.resource : `${repo}/${flow.branch}`;
}

/** One of a chunk's places as a card or a list row says it. */
export interface FlowPlaceLine {
  readonly flowId: string;
  /** `landing on blizzard/master`, `3rd on blizzard/master`, `passed over on blizzard/master`, or
   * `awaiting merge on blizzard/master`. */
  readonly text: string;
  readonly tone: Tone;
}

/** How a chunk's place on one flow reads: landing on it, passed over on it, awaiting its change's merge on it, or its position in line. */
export function placeText(place: ChunkFlowPlaceView): string {
  const path = flowPath(place);
  if (place.state === 'landing') return `landing on ${path}`;
  if (place.state === 'passed-over') return `passed over on ${path}`;
  if (place.state === 'awaiting-merge') return `awaiting merge on ${path}`;
  return `${ordinal(place.position)} on ${path}`;
}

/** Every place a chunk holds, one line per flow, in the order the hub lists them. */
export function placeLines(places: readonly ChunkFlowPlaceView[] | undefined): readonly FlowPlaceLine[] {
  return (places ?? []).map((place) => ({
    flowId: place.flow_id,
    text: placeText(place),
    tone: placeStateTone(place.state),
  }));
}
