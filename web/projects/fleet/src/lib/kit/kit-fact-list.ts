import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

/** What every row of a {@link KitFactList} carries, whichever kind it is. */
interface KitFactRow {
  readonly label: string;
  /** The row's `<dd>`'s own `data-testid`, or omitted for none. */
  readonly testid?: string;
}

/** A row whose value is plain text — the common case. */
export interface KitFactText extends KitFactRow {
  readonly value: string;
  readonly template?: never;
}

/**
 * A row whose value is markup rather than text — a component, a link, a
 * conditional fallback. The `<ng-template>` is declared in the consumer's own
 * view and rendered into the `<dd>` this component emits.
 */
export interface KitFactTemplated extends KitFactRow {
  readonly value?: never;
  readonly template: TemplateRef<unknown>;
}

/** One label/value row of a {@link KitFactList} — text or markup, never both, never neither. */
export type KitFact = KitFactText | KitFactTemplated;

/**
 * The aligned label/value fact grid — `chunk-facts.css`'s own `.kv` (`Status`/`Node`/
 * `Runner`/…), lifted into the kit so every consumer renders the same two-column
 * `<dl>` without re-typing its grid.
 *
 * Takes `rows` as plain data rather than projecting `<dt>`/`<dd>` through
 * `<ng-content>` — the shortcut someone will reach for later, and the one that
 * silently does not work: under Angular's emulated view encapsulation, a projected
 * element keeps its *consumer's* encapsulation attribute, not this component's own,
 * so a `.kv dt`/`.kv dd` selector scoped here would never match content projected in
 * from outside, and the styling would just fail to apply with no error. Rendering the
 * `<dt>`/`<dd>` pairs from `rows` inside this component's own template sidesteps that
 * entirely.
 *
 * A row whose value is not a plain string — a `fleet-when`, a revision beside a
 * conditional fallback — supplies a {@link TemplateRef} instead of a `value`, and it
 * lands in the same `<dd>` through `ngTemplateOutlet` (`kit-menu.ts` passes its panel
 * the same way, for its own reason). This splits the two halves exactly where they
 * belong: the `<dt>`/`<dd>` elements are still emitted here, so the grid, alignment
 * and typography stay this component's alone, while the markup *inside* the `<dd>`
 * keeps the consumer's encapsulation attribute and stays styleable by the consumer's
 * own rules — the very property that defeats `<ng-content>`, turned to account.
 *
 * `ChunkFacts` still keeps its own copy of the grid — its Graph row, a routerLink
 * beside a conditional inline editor, is now expressible as a templated row, so what
 * remains is the conversion itself.
 */
@Component({
  selector: 'fleet-kit-fact-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgTemplateOutlet],
  templateUrl: './kit-fact-list.html',
  styleUrl: './kit-fact-list.css',
})
export class KitFactList {
  readonly rows = input.required<readonly KitFact[]>();
  readonly testid = input<string | null>(null);

  /**
   * The rows to render, less any that fails to supply exactly one of `value` or
   * `template`. {@link KitFact}'s union already rules both malformed rows out at the
   * type level; this catches the row that reaches here anyway — a list assembled
   * dynamically, a cast — because the alternative is a blank `<dd>` sitting in the
   * grid looking like a legitimately empty fact.
   *
   * The bad row is dropped and reported, never thrown on. This runs inside a
   * `computed`, and a signal computation that throws caches the error and rethrows it
   * on every later read, so one malformed row would take down every view up the tree
   * that touches this list rather than the one `<dd>` it describes — a far heavier
   * failure than the blank cell being guarded against. The `console.error` is what
   * makes the mistake findable; the surrounding facts still render.
   */
  protected readonly checkedRows = computed<readonly KitFact[]>(() =>
    this.rows().filter((row) => {
      const wellFormed = (row.value !== undefined) !== (row.template !== undefined);
      if (!wellFormed) {
        console.error(`fact row "${row.label}" must supply exactly one of value or template; row dropped`);
      }
      return wellFormed;
    }),
  );
}
