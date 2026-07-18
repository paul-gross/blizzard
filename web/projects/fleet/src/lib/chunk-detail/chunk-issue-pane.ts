import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { PmItemEntry } from '../api/hub';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';

/** The chunk's related PM items and the state of the pass-through fetch, for the work-item column.
 *
 * `loading` while the forge read is in flight, `error` when the whole read failed (an
 * unreachable hub or no work-source configured — the pane shows a visible notice, AC5), and
 * `success` with `items` (possibly empty for a chunk with no pointers — the empty state, AC4;
 * a per-item `error` carries a single pointer's forge failure the pane notices in place). */
export interface PmItemsState {
  readonly status: 'loading' | 'error' | 'success';
  readonly items: readonly PmItemEntry[];
}

/**
 * The work item's PM issue pass-through (issue #24, issue #79) — the chunk's
 * linked forge issue(s): title, body, and messages. Owns its own
 * loading/error/empty triad through the shared kit's async-state component
 * (issue #78) rather than a re-typed `<p class="status">`. Presentational
 * only; the forge read itself lives in the container.
 */
@Component({
  selector: 'fleet-chunk-detail-issue-pane',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  template: `
    <div class="wrap" data-testid="issue-pane">
      <fleet-kit-async-state
        [state]="triadState()"
        loadingText="Loading issue…"
        loadingTestid="issue-loading"
        errorText="Could not reach the forge — issue content is unavailable."
        errorTestid="issue-error"
        emptyText="This chunk has no linked issue."
        emptyTestid="issue-empty"
      >
        @for (item of pmItems().items; track item.source + ':' + item.ref) {
          <article class="issue" data-testid="issue-item">
            <!-- The link out to the PM lives here and only here: the board cards
                 are click targets for opening a chunk, so an anchor on them
                 competes with that. This is where the operator leaves for the forge. -->
            <div class="i-head">
              <a
                class="i-label"
                data-testid="issue-label"
                [href]="item.web_url"
                target="_blank"
                rel="noreferrer"
                [attr.title]="item.web_url"
              >{{ item.label ?? (item.source + '#' + item.ref) }}</a>
            </div>
            @if (item.error) {
              <p class="notice" data-testid="issue-item-error">
                Could not load this issue — {{ item.error }}
              </p>
            } @else {
              @if (item.title) {
                <p class="i-title" data-testid="issue-title">{{ item.title }}</p>
              }
              <pre class="i-body" data-testid="issue-body">{{ item.body }}</pre>
              <div class="i-messages">
                <div class="s-head"><span class="tag">Messages · {{ (item.comments ?? []).length }}</span></div>
                @if ((item.comments ?? []).length === 0) {
                  <p class="none" data-testid="issue-no-messages">No messages.</p>
                } @else {
                  <ul class="messages" data-testid="issue-messages">
                    @for (c of item.comments ?? []; track $index) {
                      <li class="message" data-testid="issue-message"><pre class="m-body">{{ c }}</pre></li>
                    }
                  </ul>
                }
              </div>
            }
          </article>
        }
      </fleet-kit-async-state>
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    /* The async-state triad's status line centers within the nearest positioned
       ancestor (kit-async-state.ts) — this is it, with enough height for the
       centered text not to look stranded before any issue content lands. */
    .wrap {
      position: relative;
      min-height: 40px;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .s-head {
      margin-bottom: 6px;
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .notice {
      margin: 0;
      padding: 4px 6px;
      border: 1px solid var(--red-dim);
      border-left-width: 2px;
      background: var(--overlay-20);
      color: var(--red);
      font-size: var(--fs-xs);
    }
    .issue {
      border: 1px solid var(--line);
      background: var(--overlay-20);
      padding: 5px 6px;
    }
    .issue + .issue {
      margin-top: 6px;
    }
    .i-head {
      margin-bottom: 4px;
    }
    .i-label {
      color: var(--cyan);
      font-size: var(--fs-sm);
      text-decoration: none;
      overflow-wrap: anywhere;
    }
    .i-label:hover {
      text-decoration: underline;
    }
    .i-title {
      margin: 2px 0 6px;
      color: var(--text);
      font-size: var(--fs-sm);
      line-height: 1.45;
    }
    .i-body {
      margin: 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--overlay-30);
      color: var(--text);
      font-size: var(--fs-sm);
    }
    .i-messages {
      margin-top: 6px;
    }
    .messages {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .m-body {
      margin: 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--line);
      background: var(--overlay-25);
      color: var(--text);
      font-size: var(--fs-sm);
    }
  `,
})
export class ChunkIssuePane {
  /** The chunk's related PM items + fetch state, from the container (issue #24).
   * Defaults to `loading` so the pane constructs without the container wiring it. */
  readonly pmItems = input<PmItemsState>({ status: 'loading', items: [] });

  /** The async triad's resolved state — loading/error take precedence, then no
   * linked issue, else the issue items render. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    const state = this.pmItems();
    if (state.status === 'loading') return 'loading';
    if (state.status === 'error') return 'error';
    return state.items.length === 0 ? 'empty' : 'ready';
  });
}
