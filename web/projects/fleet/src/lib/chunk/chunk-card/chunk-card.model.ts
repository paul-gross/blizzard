import type { ChunkStatus, LandedRepoView, PrView, WorkItemsView, WorkRefView } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import type { Tone } from '../../kit';

/** A group past this many titles folds to its first {@link FOLDED_TITLES} until opened. */
const UNFOLDED_TITLES = 3;
const FOLDED_TITLES = 2;

/** The statuses a card leaves unsaid: RUNNING, READY, BACKLOG, and NEEDS YOU each hold one as
 * their lane's namesake, and `delivering` is already said by the hub node the card's step names.
 * The rest — paused beside waiting_on_human — say more than their lane does, as does any status
 * added later until it is named here. */
const UNSAID_STATUSES: ReadonlySet<ChunkStatus> = new Set(['running', 'ready', 'not_ready', 'needs_human', 'delivering']);

/** One work item as a card names it: its work-ref label over its title. Either may be absent —
 * a pointer naming no configured source has no label, and the title is `null` until read. */
export interface WorkLine {
  readonly label: string | null;
  readonly title: string | null;
  /** The item's page at its work source, where the source links one. */
  readonly url?: string | null;
}

/** Every work item as a label-and-title pair, in pointer order, each pointer matched to its read
 * item by source and ref. Every pointer holds its line from the start, so a card keeps its
 * height as the titles arrive: a labeled one names its label, its title `null` until read and
 * left `null` for an item with no title rather than repeating the label it sits under; an
 * unlabeled one holds an empty line until read, then names its item by its title or else the
 * item's own label, and drops out if it has neither. An item read under no pointer at all still
 * names itself, so nothing the chunk carries goes unsaid. */
export function workLines(refs: readonly WorkRefView[], view: WorkItemsView | undefined): readonly WorkLine[] {
  const items = view?.items ?? [];
  const matched = (ref: WorkRefView) => items.find((each) => each.source === ref.source && each.ref === ref.ref);
  const lines: WorkLine[] = refs.flatMap((ref): WorkLine[] => {
    const item = matched(ref);
    const url = ref.web_url ?? item?.web_url ?? null;
    if (ref.label) return [{ label: ref.label, title: item?.title || null, url }];
    if (view === undefined) return [{ label: null, title: null, url }];
    const title = item?.title || item?.label;
    return title ? [{ label: null, title, url }] : [];
  });
  for (const item of items) {
    const pointed = refs.some((ref) => ref.source === item.source && ref.ref === item.ref);
    const title = item.title || item.label;
    if (!pointed && title) lines.push({ label: null, title, url: item.web_url });
  }
  return lines;
}

/** The titles a group shows: every one up to three, else the first two until opened, with
 * how many the fold hides. */
export function foldTitles<T>(titles: readonly T[], expanded: boolean): { shown: readonly T[]; hidden: number } {
  const shown = titles.length <= UNFOLDED_TITLES || expanded ? titles : titles.slice(0, FOLDED_TITLES);
  return { shown, hidden: titles.length - shown.length };
}

/** Whether a card names its status — only where it says more than the lane does. */
export function cardShowsStatus(status: ChunkStatus): boolean {
  return !UNSAID_STATUSES.has(status);
}

/** The word a finished chunk is named by — `Stopped` for a stopped chunk, `Completed` for any
 * other the DONE lane holds. */
export function finishedWord(status: ChunkStatus): string {
  return status === 'stopped' ? 'Stopped' : 'Completed';
}

/** A card's step: the current node's name, or `stopped` for a stopped chunk, whose last
 * node is noise once it has stopped. */
export function stepLabel(status: ChunkStatus, node: string): string {
  return status === 'stopped' ? 'stopped' : node;
}

/** What a blocked marking names: the one unmet prerequisite's compact ref, or a count once
 * more than one is unmet. */
export function blockedLabel(blockedOn: string | null, blockedCount: number): string {
  return blockedCount > 1 ? `${blockedCount} chunks` : compactRef(blockedOn ?? '');
}

/** One place a chunk holds on a delivery flow, as a card or row draws it: its line, in its state's tone. */
export interface ChunkCardPlace {
  /** What keeps the place distinct among the chunk's places — the flow it is on. */
  readonly key: string;
  readonly text: string;
  readonly tone: Tone;
  readonly title: string;
}

/** The runner holding a chunk, as a card or row names it. */
export interface ChunkCardHolder {
  /** The holder's host, clipped first — `null` where it is not told. */
  readonly host: string | null;
  readonly name: string;
  readonly title: string;
}

/** A chunk's blocked marking: what it names, and the blocker's own status beside it. */
export interface ChunkCardBlocked {
  /** The one unmet prerequisite's compact ref, or the count once more than one is unmet ({@link blockedLabel}). */
  readonly label: string;
  /** Whether {@link label} is a count rather than a ref. */
  readonly counted: boolean;
  readonly title: string;
  /** The single blocker's own status, or `null` where none, or more than one, is named. */
  readonly state: string | null;
}

/**
 * One chunk as the fleet card and list row draw it — presentation data each app derives from its own reads,
 * never a mirror of one of them (`bzh:frontend-wire-conformist`). Status arrives as a word and a {@link Tone}, never
 * as a status vocabulary of its own; the card's edge and id are drawn in that tone. A region only one app fills is
 * optional, and renders nothing without its data.
 */
export interface ChunkCardView {
  readonly chunkId: string;
  /** The chunk's compact id, which ends the card's step row and heads the row's work. */
  readonly shortId: string;
  /** The status word the card is marked with — the `data-status` handle and the status's hover text. */
  readonly status: string;
  /** The status's tone — the card's edge and id colour. */
  readonly tone: Tone;
  /** The status word the step row names, or `null` where it would only repeat the lane. */
  readonly statusWord: string | null;
  /** The step the chunk is on. */
  readonly step: string;
  /** The step's hover text, or `null` for none. */
  readonly stepTitle: string | null;
  /** The chunk's work items, each a label over its title. */
  readonly workLines: readonly WorkLine[];
  /** Whether the work items have been read — until then each title holds a skeleton. */
  readonly itemsRead: boolean;
  /** The runner holding the chunk, or `null` where none does. */
  readonly holder?: ChunkCardHolder | null;
  readonly blocked?: ChunkCardBlocked | null;
  /** Whether the card draws its delivery block — its places, an awaited merge, and its open PRs. */
  readonly showsDelivery?: boolean;
  readonly places?: readonly ChunkCardPlace[];
  readonly awaitingExternalMerge?: boolean;
  readonly openPrs?: readonly PrView[];
  readonly landedRepos?: readonly LandedRepoView[];
  /** The chunk's spend, already formatted, or `null` for none to show. */
  readonly cost?: string | null;
  /** When a finished chunk finished, or `null` for one not finished. */
  readonly completedAt?: string | null;
  /** The word a finished chunk is named by on its row's closing line ({@link finishedWord}). */
  readonly finishedWord?: string | null;
  /** The status a list row names in its trailing cell, where it names one, or `null`. */
  readonly rowStatus?: string | null;
  /** Whether the chunk may be promoted — with the card's `canControl`, offers Promote. */
  readonly promotable?: boolean;
  readonly projectName?: string | null;
  readonly projectColor?: string | null;
}
