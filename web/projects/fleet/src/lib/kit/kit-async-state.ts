import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** The four states a query-backed read renders through — the fourth,
 * `'ready'`, projects the caller's own content instead of a status line. */
export type KitAsyncStateValue = 'loading' | 'error' | 'empty' | 'ready';

/**
 * The async-state triad (issue #78) — the loading/error/empty status line
 * every read-backed panel duplicated (`local-panel`'s byte-for-byte `.status`
 * block), plus a `'ready'` state that projects the caller's populated content
 * instead. Presentational: it renders whichever state it is handed and reads
 * no query itself.
 *
 * `:host { display: contents }` so this component contributes no box of its
 * own — the status line's `position: absolute` centering resolves against
 * whichever positioned ancestor the *caller* already provides (its own
 * `:host`, or a wrapping element), exactly as it did before extraction.
 *
 * `tone` covers a state that reads with a variant color, distinct from the
 * plain default (dim) and `'error'` (red) — e.g. a "not available yet, but
 * that's expected" message in the accent color rather than the alarm color.
 */
@Component({
  selector: 'fleet-kit-async-state',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @switch (state()) {
      @case ('ready') {
        <ng-content />
      }
      @case ('loading') {
        <p class="status" [attr.data-testid]="loadingTestid()">{{ loadingText() }}</p>
      }
      @case ('error') {
        <p class="status error" [attr.data-testid]="errorTestid()">{{ errorText() }}</p>
      }
      @case ('empty') {
        <p class="status" [class.accent]="tone() === 'accent'" [attr.data-testid]="emptyTestid()">{{ emptyText() }}</p>
      }
    }
  `,
  styles: `
    :host {
      display: contents;
    }
    .status {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      white-space: nowrap;
      color: var(--label-dim);
      font-size: var(--fs-sm);
      letter-spacing: 0.12em;
    }
    .status.error {
      color: var(--red);
    }
    .status.accent {
      color: var(--cyan);
    }
  `,
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

  /** Each state's rendered `data-testid`, or `null` for none — every consumer
   * names its own (they differ per caller, and only one state is ever
   * rendered at a time), so browser-tier locators stay unambiguous. */
  readonly loadingTestid = input<string | null>(null);
  readonly errorTestid = input<string | null>(null);
  readonly emptyTestid = input<string | null>(null);
}
