import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';

import { KitBadge, KitField, KitOption, KitOptionGroup } from 'fleet';

import type { Scope } from '../core/scope.domain';

/**
 * The gardening run dialog's scope field.
 * Lists the routine's own related, non-retired scopes, previously-swept first, as an option group bound to the
 * draft's scope field.
 *
 * Presentational only: renders `scopes()` in the order given under the field tree it is handed.
 */
@Component({
  selector: 'app-gardening-run-scope-field',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitBadge, KitField, KitOption, KitOptionGroup],
  templateUrl: './gardening-run-scope-field.html',
  styleUrl: './gardening-run-scope-field.css',
})
export class GardeningRunScopeField {
  readonly field = input.required<FieldTree<string>>();

  /** The routine's own related, non-retired scopes, previously-swept-by-this-routine
   * first; rendered in the order given. */
  readonly scopes = input.required<readonly Scope[]>();

  /** The scope slugs this routine has previously swept — renders a "previously swept"
   * marker per row; membership only, the ordering itself already lives in `scopes()`. */
  readonly sweptSlugs = input.required<ReadonlySet<string>>();

  protected isSweptSlug(slug: string): boolean {
    return this.sweptSlugs().has(slug);
  }
}
