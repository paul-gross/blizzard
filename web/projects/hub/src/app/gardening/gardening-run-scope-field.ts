import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitOption, KitPanel, type ScopeView } from 'fleet';

/** The run dialog's own scope-field state — the chosen scope's slug, `''` for nothing
 * selected yet (blizzard#399 phase 1: the field offers only the routine's own related
 * set, so there is no longer a slug the operator can mint here). */
export type ScopeSelection = string;

export const EMPTY_SCOPE_SELECTION: ScopeSelection = '';

/**
 * The gardening run dialog's scope field (blizzard#399 D6) — split out of
 * {@link GardeningRunDialogView} ahead of the 400-line ceiling rather than after it.
 * Lists the routine's own related, non-retired scopes, previously-swept first.
 *
 * Presentational only: renders `scopes()` (the container's own ordering, D5) and
 * `selection()`, and re-emits every change through `selectionChange` — the container
 * decides what a selection means.
 */
@Component({
  selector: 'app-gardening-run-scope-field',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitOption, KitPanel],
  templateUrl: './gardening-run-scope-field.html',
  styleUrl: './gardening-run-scope-field.css',
})
export class GardeningRunScopeField {
  /** The routine's own related, non-retired scopes, previously-swept-by-this-routine
   * first (D5, D6) — the container's own ordering; this field renders it verbatim. */
  readonly scopes = input.required<readonly ScopeView[]>();

  /** The scope slugs this routine has previously swept — renders a "previously swept"
   * marker per row; membership only, the ordering itself already lives in `scopes()`. */
  readonly sweptSlugs = input.required<ReadonlySet<string>>();

  readonly selection = input.required<ScopeSelection>();

  readonly selectionChange = output<ScopeSelection>();

  protected selectExisting(slug: string): void {
    this.selectionChange.emit(slug);
  }

  protected isSweptSlug(slug: string): boolean {
    return this.sweptSlugs().has(slug);
  }
}
