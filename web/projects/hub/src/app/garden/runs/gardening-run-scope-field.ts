import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitOption, KitPanel, type ScopeView } from 'fleet';

/** The run dialog's own scope-field state — the chosen scope's slug, `''` for nothing
 * selected yet (the field offers only the routine's own related
 * set). */
export type ScopeSelection = string;

export const EMPTY_SCOPE_SELECTION: ScopeSelection = '';

/**
 * The gardening run dialog's scope field.
 * Lists the routine's own related, non-retired scopes, previously-swept first.
 *
 * Presentational only: renders `scopes()` in the order given and `selection()`,
 * and re-emits every change through `selectionChange`.
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
   * first; rendered in the order given. */
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
