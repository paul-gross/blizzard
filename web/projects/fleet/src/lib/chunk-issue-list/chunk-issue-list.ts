import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { WorkItemAuthorView, WorkItemEntry } from '../api/hub';
import { KitAccordionSection, KitAsyncState, KitBadge, KitMarkdown } from '../kit';
import type { Tone } from '../kit';

/** A stated priority's badge tone (blizzard#362) — advice for the triaging human, never
 * a queue position, so it borrows the shared urgency ladder rather than inventing one. */
const PRIORITY_TONE: Record<string, Tone> = {
  high: 'needs',
  normal: 'waiting',
  low: 'idle',
};

/** The hub source's own reserved name (`RESERVED_HUB_SOURCE_NAME`, `hub/config.py`) —
 * every other `source` a pointer can carry is an operator-configured forge. Unlike
 * `author`/`web_url`, `source` is a required wire field, present on every entry
 * regardless of fetch outcome or the pointer's holding chunk's own status. */
const HUB_SOURCE_NAME = 'hub';

/**
 * A chunk's resolved work items as a one-line-per-issue accordion — the
 * ticket name first (the thing worth reading, carrying the visual weight)
 * then the work ref to its right as `- source#ref`, an independently
 * clickable address. Each row's own header is the accordion trigger;
 * expanding it reveals the item's own idiom (blizzard#362) — a forge
 * pointer's title/body/messages, or a hub pointer's markdown body,
 * authorship line, and stated priority, discriminated by
 * {@link WorkItemEntry.source source} — the one field every entry carries
 * regardless of fetch outcome.
 *
 * Daemon-agnostic ({@link ChunkTimeline}/{@link ChunkArtifactsPanel}'s own
 * shape): inputs off the shared `WorkItemEntry` wire type alone, no
 * injection, so both the hub and the runner mount it verbatim. Presentational
 * only — {@link items} is already the resolved, successful read; the
 * loading/error/empty triad around that read stays the caller's own
 * (`ChunkIssuePane`'s `fleet-kit-async-state` wrapper), so this component
 * never sees an empty array in practice and holds no empty-state rendering
 * of its own.
 *
 * Default expansion (`bzh` per-instance rule, not a config the caller passes
 * in — it is purely a function of how many issues there are): a lone issue
 * starts expanded, since there is nothing else on the row to scan past; two
 * or more start collapsed, so a grouped chunk's issue list reads as a list of
 * headers first. That default is *only* a default — {@link KitAccordionSection}
 * is fully controlled, so the operator toggling one section leaves the rest
 * exactly as they were, and more than one section can read open at once.
 */
@Component({
  selector: 'fleet-chunk-issue-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAccordionSection, KitAsyncState, KitBadge, KitMarkdown, RouterLink],
  templateUrl: './chunk-issue-list.html',
  styleUrl: './chunk-issue-list.css',
})
export class ChunkIssueList {
  /** The chunk's resolved work items — already past the loading/error/empty
   * triad, one entry per pointer. */
  readonly items = input.required<readonly WorkItemEntry[]>();

  /** The chunk detail route's own path segments, before a chunk id (blizzard#362) —
   * lets a consumer outside the desktop board point a fleet author's chunk link
   * elsewhere without `fleet` hardcoding a hub route (`ChunkDetailHeader`'s own
   * `linkBase` follows the same convention). */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Which sections the operator has explicitly toggled, keyed by
   * {@link keyFor} — absent from this map, a section reads whatever
   * {@link defaultExpanded} says. A plain signal rather than one input/output
   * pair per section: nothing outside this list needs to know or drive which
   * issue is open, so the state stays local UI state, not a query result. */
  private readonly expandedOverrides = signal<ReadonlyMap<string, boolean>>(new Map());

  /** The stable per-item key {@link KitAccordionSection.sectionId} and the
   * override map both key on — the same `source:ref` pair the pre-accordion
   * card's own `@for` tracked by. */
  protected keyFor(item: WorkItemEntry): string {
    return `${item.source}:${item.ref}`;
  }

  /** The ref half of the row, `source#ref` — {@link WorkItemEntry.label} when
   * the pointer's source resolved one, else the raw pair assembled by hand. */
  protected refText(item: WorkItemEntry): string {
    return item.label ?? `${item.source}#${item.ref}`;
  }

  /** The name half of the row. `'—'` — the app's own placeholder for a
   * missing legible name (`chunk-facts.html`'s node-name fallback) — stands
   * in for a null/blank title, the toy-api-style live case where the forge
   * read itself failed and left `title` unset: a bare `- toyapi#1` with
   * nothing before the dash would read as broken, not as "no title". */
  protected nameText(item: WorkItemEntry): string {
    return item.title?.trim() || '—';
  }

  /** Which idiom an entry renders in (blizzard#362) — `source` discriminates, the one
   * field every entry carries regardless of fetch outcome. `author` and `web_url` were
   * each tried first and each has a combination where it goes absent for a genuine hub
   * entry — `author` on any errored fetch, `web_url` once no live chunk holds the
   * pointer, and *both at once* for an errored fetch with no live holder — so no
   * optional-field combination is a safe discriminator; `source` is required on the
   * wire and never null. */
  protected isHubEntry(item: WorkItemEntry): boolean {
    return item.source === HUB_SOURCE_NAME;
  }

  /** A stated priority's badge tone, or `null` for one this list does not recognize
   * (defensive against a future value the wire has not widened this component for). */
  protected priorityTone(item: WorkItemEntry): Tone | null {
    return item.stated_priority ? (PRIORITY_TONE[item.stated_priority] ?? null) : null;
  }

  /** A `user`-authored item's legible name — the resolved login, falling back to the
   * bare id only when the hub source could not resolve one (a deleted user). */
  protected userLabel(author: WorkItemAuthorView): string {
    return author.login ?? author.user_id ?? 'someone';
  }

  /** Whether `item`'s section reads open right now — the operator's own
   * toggle when they have made one, else the default rule: expanded alone,
   * collapsed among siblings. */
  protected isExpanded(item: WorkItemEntry): boolean {
    const override = this.expandedOverrides().get(this.keyFor(item));
    return override ?? this.items().length === 1;
  }

  /** Records the operator's toggle for `item`, leaving every other section's
   * own state (default or already-toggled) untouched. */
  protected setExpanded(item: WorkItemEntry, expanded: boolean): void {
    const next = new Map(this.expandedOverrides());
    next.set(this.keyFor(item), expanded);
    this.expandedOverrides.set(next);
  }
}
