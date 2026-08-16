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
 *
 * `placement` picks the status line's layout: `'center'` (default) keeps the
 * original `position: absolute` centering, right for a panel-sized void
 * (board, chunk dock); `'inline'` renders the same states in normal flow with
 * left-aligned padding, right for a list panel whose existing `.none` copy sat
 * as a padded top-left line — adopting the kit there is not a silent visual
 * regression.
 *
 * `loadingMode` picks what the `loading` state renders: `'text'` (default)
 * keeps the status line; `'content'` instead projects the `[loading]`-slotted
 * content the caller supplies (typically a `KitSkeleton`) — a shape-of-what's-
 * coming placeholder rather than a status line, purely a polish increment
 * over the text every acceptance criterion is already met by.
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
        @if (loadingMode() === 'content') {
          <ng-content select="[loading]" />
        } @else {
          <p class="status" [class.inline]="placement() === 'inline'" [attr.data-testid]="loadingTestid()">{{ loadingText() }}</p>
        }
      }
      @case ('error') {
        <p class="status error" [class.inline]="placement() === 'inline'" [attr.data-testid]="errorTestid()">{{ errorText() }}</p>
      }
      @case ('empty') {
        <p
          class="status"
          [class.inline]="placement() === 'inline'"
          [class.accent]="tone() === 'accent'"
          [attr.data-testid]="emptyTestid()"
        >{{ emptyText() }}</p>
      }
    }
  `,
  styles: `
    :host {
      display: contents;
    }
    /* Horizontal centering is left:0/right:0 (not left:50%/translateX(-50%)):
       for an absolutely-positioned box with width:auto, the CSS shrink-to-fit
       algorithm sizes against the space between left and the containing
       block's edge — left:50% leaves only the right half to shrink-fit
       against, so a max-width set against the full container never actually
       binds and long text wraps at half width instead of the full width it
       reads as centered within. left:0/right:0 makes the box exactly the
       container's width up front, so centered text wraps at the real edge. */
    .status {
      position: absolute;
      left: 0;
      right: 0;
      top: 50%;
      transform: translateY(-50%);
      text-align: center;
      color: var(--label-dim);
      font-size: var(--fs-sm);
      letter-spacing: 0.12em;
    }
    .status.inline {
      position: static;
      left: auto;
      right: auto;
      top: auto;
      transform: none;
      text-align: left;
      display: block;
      padding: 10px 8px;
      margin: 0;
      font-size: var(--fs-sm);
      letter-spacing: 0.08em;
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
