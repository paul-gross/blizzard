import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** The four states a query-backed read renders through — the fourth,
 * `'ready'`, projects the caller's own content instead of a status line. */
export type KitAsyncStateValue = 'loading' | 'error' | 'empty' | 'ready';

/**
 * The async-state triad — the loading/error/empty status line a
 * read-backed panel shows, plus a `'ready'` state that projects the caller's populated content
 * instead. Presentational: it renders whichever state it is handed and reads
 * no query itself.
 *
 * `:host { display: contents }` so this component contributes no box of its
 * own — the status line's `position: absolute` centering resolves against
 * whichever positioned ancestor the *caller* provides (its own `:host`, or a
 * wrapping element).
 *
 * `tone` covers a state that reads with a variant color, distinct from the
 * plain default (dim) and `'error'` (red) — e.g. a "not available yet, but
 * that's expected" message in the accent color rather than the alarm color.
 *
 * `placement` picks the status line's layout: `'center'` (default) uses
 * `position: absolute` centering, right for a panel-sized void; `'inline'`
 * renders the same states in normal flow with left-aligned padding, right for
 * a list panel whose empty copy reads as a padded top-left line.
 *
 * `loadingMode` picks what the `loading` state renders: `'text'` (default)
 * keeps the status line; `'content'` instead projects the `[loading]`-slotted
 * content the caller supplies (typically a `KitSkeleton`) — a shape-of-what's-
 * coming placeholder rather than a status line.
 */
@Component({
  selector: 'fleet-kit-async-state',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-async-state.html',
  styleUrl: './kit-async-state.css',
})
export class KitAsyncState {
  /** Which of the four states to render right now. */
  readonly state = input.required<KitAsyncStateValue>();

  readonly loadingText = input('LOADING…');
  readonly errorText = input('UNAVAILABLE');
  readonly emptyText = input('NOTHING HERE');

  /** `'accent'` colors the `empty` state's text in `--cyan` instead of the
   * default dim label color — for an expected, in-progress "not here yet"
   * reading distinct from both the default empty state and a fault. */
  readonly tone = input<'default' | 'accent'>('default');

  /** `'center'` (default) keeps the status line absolutely centered in a
   * positioned ancestor; `'inline'` renders it left-aligned in normal flow,
   * padded like the list-panel `.none` copy it replaces. */
  readonly placement = input<'center' | 'inline'>('center');

  /** `'text'` (default) renders `loadingText()`; `'content'` projects the
   * caller's `[loading]`-slotted content instead. */
  readonly loadingMode = input<'text' | 'content'>('text');

  /** Each state's rendered `data-testid`, or `null` for none — every consumer
   * names its own (they differ per caller, and only one state is ever
   * rendered at a time), so browser-tier locators stay unambiguous. */
  readonly loadingTestid = input<string | null>(null);
  readonly errorTestid = input<string | null>(null);
  readonly emptyTestid = input<string | null>(null);
}
