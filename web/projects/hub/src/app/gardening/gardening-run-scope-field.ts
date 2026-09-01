import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { KitOption, KitPanel, type ScopeView } from 'fleet';

/** The run dialog's own scope-field state — an existing slug, or a new one the
 * operator is minting alongside its description (D3, D4). */
export interface ScopeSelection {
  readonly slug: string;
  readonly isNew: boolean;
  /** Only meaningful while `isNew` — the description `POST /api/scopes` mints the
   * slug with (`GardeningRunDialog.onSubmit`'s own fact is D3's create-then-run
   * ordering, not restated here). */
  readonly newDescription: string;
}

export const EMPTY_SCOPE_SELECTION: ScopeSelection = { slug: '', isNew: false, newDescription: '' };

/** Levenshtein edit distance — the near-match warning's own metric (D4): cheap,
 * dependency-free, and forgiving of a typo the length-based checks below would miss. */
function editDistance(a: string, b: string): number {
  const rows = a.length + 1;
  const cols = b.length + 1;
  const d: number[][] = Array.from({ length: rows }, (_, i) => [i, ...Array<number>(cols - 1).fill(0)]);
  for (let j = 0; j < cols; j += 1) d[0][j] = j;
  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      d[i][j] =
        a[i - 1] === b[j - 1] ? d[i - 1][j - 1] : 1 + Math.min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1]);
    }
  }
  return d[rows - 1][cols - 1];
}

/** The edit-distance branch's own floor — below this, a distance of 2 covers too much
 * of the string to mean anything ("hub" vs. "web" is 2 substitutions out of 3
 * characters, not a plain typo); the substring branch above still catches a short
 * slug that is genuinely a prefix/suffix of an existing one. */
const MIN_EDIT_DISTANCE_LENGTH = 5;

/** Whether `typed` reads close enough to `existing` to warn about (D4) — an exact
 * match ignoring case/separators, a substring either way (3+ characters), or — for
 * slugs long enough for it to mean something — a small edit distance for a plain
 * typo. Both sides are trimmed first: the caller's own length gate already trims, and
 * comparing the untrimmed value would let trailing whitespace shift the computed
 * distance out from under it. */
function isNearMatch(typed: string, existing: string): boolean {
  const norm = (s: string) => s.trim().toLowerCase().replace(/[-_]/g, '');
  const nt = norm(typed);
  const ne = norm(existing);
  if (!nt || !ne) return false;
  if (nt === ne) return true;
  if (nt.length >= 3 && (ne.includes(nt) || nt.includes(ne))) return true;
  if (nt.length < MIN_EDIT_DISTANCE_LENGTH || ne.length < MIN_EDIT_DISTANCE_LENGTH) return false;
  return editDistance(nt, ne) <= 2;
}

/**
 * The gardening run dialog's scope field (blizzard#399 D6) — split out of
 * {@link GardeningRunDialogView} ahead of the 400-line ceiling rather than after it.
 * Lists every non-retired scope, previously-swept first, with a mint-a-new-slug escape
 * hatch that requires a description and warns — client-side, advisory (D4) — on a
 * near-match before the caller ever commits it.
 *
 * Presentational only: renders `scopes()` (the container's own ordering, D5) and
 * `selection()`, and re-emits every change through `selectionChange` — the container
 * decides what a selection means (D3's create-then-run).
 */
@Component({
  selector: 'app-gardening-run-scope-field',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitOption, KitPanel],
  templateUrl: './gardening-run-scope-field.html',
  styleUrl: './gardening-run-scope-field.css',
})
export class GardeningRunScopeField {
  /** Every non-retired scope, previously-swept-by-this-routine first (D5) — the
   * container's own ordering; this field renders it verbatim. */
  readonly scopes = input.required<readonly ScopeView[]>();

  /** The scope slugs this routine has previously swept — renders a "previously swept"
   * marker per row; membership only, the ordering itself already lives in `scopes()`. */
  readonly sweptSlugs = input.required<ReadonlySet<string>>();

  /** Every scope's slug, retired included — the near-match check reads this,
   * not `scopes()`: a retired scope is never a selectable row, but its exact name
   * must still warn, or minting a duplicate-by-retirement slug gets no warning at all
   * and the run that follows refuses it outright. */
  readonly existingSlugs = input.required<ReadonlySet<string>>();

  readonly selection = input.required<ScopeSelection>();

  readonly selectionChange = output<ScopeSelection>();

  /** Near-match warnings against the typed new slug (D4) — every existing slug close
   * enough to be worth a second look, empty while an existing scope is selected or the
   * new slug is too short to say anything meaningful about. */
  protected readonly nearMatches = computed<readonly string[]>(() => {
    const sel = this.selection();
    if (!sel.isNew || sel.slug.trim().length < 2) return [];
    return Array.from(this.existingSlugs()).filter((slug) => isNearMatch(sel.slug, slug));
  });

  protected selectExisting(slug: string): void {
    this.selectionChange.emit({ slug, isNew: false, newDescription: '' });
  }

  protected selectNew(): void {
    const current = this.selection();
    this.selectionChange.emit({ slug: current.isNew ? current.slug : '', isNew: true, newDescription: current.newDescription });
  }

  protected onNewSlugInput(value: string): void {
    this.selectionChange.emit({ ...this.selection(), isNew: true, slug: value });
  }

  protected onNewDescriptionInput(value: string): void {
    this.selectionChange.emit({ ...this.selection(), isNew: true, newDescription: value });
  }

  protected isSweptSlug(slug: string): boolean {
    return this.sweptSlugs().has(slug);
  }
}
