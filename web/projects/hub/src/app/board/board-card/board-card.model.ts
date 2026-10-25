import { type ChunkStatus, compactRef, type WorkItemsView, type WorkRefView } from 'fleet';

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
