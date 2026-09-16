import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { KitBackBar } from './kit-back-bar';

/**
 * The master/detail split every two-pane list-beside-viewer tab re-typed —
 * a fixed list pane and a detail pane, each an `<ng-content>` slot
 * (`[kit-master-detail-list]` / `[kit-master-detail-detail]`, {@link ChunkPageShell}'s
 * own kebab-case, component-prefixed slot-attribute convention) rather than a shape this
 * component renders itself: the list's rows and the detail's own body stay each
 * consumer's own (`bzh:frontend-container-presentational`), this owns only the
 * chrome around them — the split itself, its optional per-pane caption, its
 * `@media (min-width: 720px)` row collapse, and the opt-in phone drill-down that
 * shows one pane at a time.
 *
 * `:host` **is** the split container, with no wrapper inside it — the same
 * projection-boundary stance {@link ChunkPageShell}'s own doc comment states: a
 * shell cannot style a node it does not render, so the element a consumer's own
 * `data-testid` names (carried on this component's host tag from outside) and the
 * element a spec measures must stay the same box.
 *
 * Fully controlled, the same shape {@link ChunkArtifactsPanel}'s own
 * `drilldown`/`hasSelection`/`showList`/`showDetail`/`back` follow: this component
 * derives no selection of its own — it cannot see what a consumer's `[kitMasterDetailDetail]`
 * content even keys on — so `hasSelection` arrives as a plain input beside `drilldown`,
 * and activating Back only emits; the consumer's own selection state is the sole writer.
 *
 * `testidPrefix` roots the Back button's own `data-testid` the same way
 * {@link ChunkArtifactsPanel.testidPrefix} roots its nav/view testids — a consumer whose
 * existing Back testid an outside caller already queries (the hub's chunk page restores
 * focus to `node-history-back` after a step pick) keeps that exact string by setting
 * `testidPrefix="node-history"`.
 *
 * A caption is optional per pane; given one, the pane reads `role="region"` labelled by
 * it, with the label's own `id` derived from the required `paneId` input the way
 * {@link KitAccordionSection.sectionId} already derives its `aria-controls`/`aria-labelledby`
 * pair — an omitted caption leaves the pane unlabelled rather than expanding today's wiring,
 * where only the list pane (`Timeline`) is ever named and the detail pane never is.
 *
 * The detail pane's own content inset is a CSS custom property
 * (`--kit-master-detail-detail-inset`), not an input, the same hook
 * {@link KitPanel}'s `--kit-panel-bg`/`--kit-panel-header-bg` already establish for
 * exactly this: neither app can apply padding to a node this component renders, and one
 * consumer (the hub's Node history tab) wants an 8px inset its sibling (the runner's) does
 * not, so the property cascades through view encapsulation from whichever consumer sets it.
 *
 * The list pane's fixed width resolves through `var(--master-list-col)`
 * (`design/tokens.css`) rather than a literal of its own, so every surface sharing this
 * shape stays the same width by construction.
 */
@Component({
  selector: 'fleet-kit-master-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBackBar],
  templateUrl: './kit-master-detail.html',
  styleUrl: './kit-master-detail.css',
})
export class KitMasterDetail {
  /** A stable id unique among a consumer's own master/detail instances — the list and
   * detail captions' own `id`s derive from it. */
  readonly paneId = input.required<string>();

  /** The list pane's caption, or `null` for none — an unset caption leaves the pane
   * with no `role="region"`/`aria-labelledby` pair at all rather than an unlabelled one. */
  readonly listCaption = input<string | null>(null);

  /** The detail pane's caption, or `null` for none. */
  readonly detailCaption = input<string | null>(null);

  /** Opt-in phone drill-down presentation. An unselected split renders only its list
   * pane; a selected one renders only its detail pane, with a Back control ahead of the
   * projected detail content. */
  readonly drilldown = input(false);

  /** Whether the consumer currently holds a detail selection — this component cannot
   * derive one from content it does not own. */
  readonly hasSelection = input(false);

  /** The Back control's label, e.g. `'Node history'`. */
  readonly backLabel = input.required<string>();

  /** Roots the Back button's own `data-testid` the same way
   * {@link ChunkArtifactsPanel.testidPrefix} roots its own testids. */
  readonly testidPrefix = input('master-detail');

  /** Back's activation — the consumer's own selection state is the sole writer, this
   * component only reports the click. */
  readonly back = output<void>();

  protected readonly showList = computed(() => !this.drilldown() || !this.hasSelection());
  protected readonly showDetail = computed(() => !this.drilldown() || this.hasSelection());

  protected readonly listCaptionId = computed(() => `${this.paneId()}-list-caption`);
  protected readonly detailCaptionId = computed(() => `${this.paneId()}-detail-caption`);
}
