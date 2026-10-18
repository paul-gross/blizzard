import { type ChunkStatus, compactRef, type WorkItemsView } from 'fleet';

/** A group past this many titles folds to its first {@link FOLDED_TITLES} until opened. */
const UNFOLDED_TITLES = 3;
const FOLDED_TITLES = 2;

/** The statuses a lane's own heading already names, so a card leaves them unsaid:
 * RUNNING, READY, BACKLOG, and NEEDS YOU each hold one of these as their namesake. The
 * rest — paused beside waiting_on_human, delivering beside running — say more than their
 * lane does, as does any status added later until it is named here. */
const LANE_NAMESAKES: ReadonlySet<ChunkStatus> = new Set(['running', 'ready', 'not_ready', 'needs_human']);

/** Every work item's title, in pointer order, an item with no title standing in by its
 * label — `null` while the chunk's work items are unread, so the card leads with its
 * work-ref labels until they arrive. */
export function workItemTitles(view: WorkItemsView | undefined): readonly string[] | null {
  if (view === undefined) return null;
  return (view.items ?? []).flatMap((item) => {
    const text = item.title ?? item.label;
    return text ? [text] : [];
  });
}

/** One line of a single-item chunk's description; `null` for a group — whose titles
 * already fill the row — or an item with no prose. */
export function workItemDescription(view: WorkItemsView | undefined): string | null {
  const items = view?.items ?? [];
  if (items.length !== 1 || !items[0].body) return null;
  return firstLine(items[0].body, items[0].title ?? null);
}

/** The first line of prose in a markdown body. Comments, fenced code, tables, and rules
 * are dropped; heading, quote, and list marks are stripped and link, code, and emphasis
 * syntax unwrapped; a line that only restates the title is skipped. */
export function firstLine(markdown: string, title: string | null): string | null {
  const restated = title?.trim();
  const lines = markdown
    .replace(/<!--[\s\S]*?(-->|$)/g, '')
    .replace(/```[\s\S]*?(```|$)/g, '')
    .split('\n')
    .filter((line) => !/^\s*\|/.test(line) && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(line))
    .map((line) =>
      line
        .replace(/^\s*(>\s*)+/, '')
        .replace(/^#{1,6}\s+/, '')
        .replace(/^\s*([-*+]|\d+\.)\s+(\[[ xX]\]\s+)?/, '')
        .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
        .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')
        .trim(),
    )
    .filter((line) => line.length > 0 && line !== restated);
  return lines[0] ?? null;
}

/** The titles a group shows: every one up to three, else the first two until opened, with
 * how many the fold hides. */
export function foldTitles(titles: readonly string[], expanded: boolean): { shown: readonly string[]; hidden: number } {
  const shown = titles.length <= UNFOLDED_TITLES || expanded ? titles : titles.slice(0, FOLDED_TITLES);
  return { shown, hidden: titles.length - shown.length };
}

/** Whether a card names its status — only where it says more than the lane does. */
export function cardShowsStatus(status: ChunkStatus): boolean {
  return !LANE_NAMESAKES.has(status);
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
