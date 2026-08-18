import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  type ArtifactView,
  ChunkArtifactBody,
  FleetWhen,
  KitAsyncState,
  type KitAsyncStateValue,
  sortArtifacts,
} from 'fleet';

/**
 * The chunk detail page's Artifacts tab (issue #160) — a nav list beside a
 * viewer, replacing the desktop dock's inline stack the way the mobile
 * shell's per-artifact page already did, but without leaving this page for a
 * deeper route: the viewer swaps in place as the operator picks a different
 * entry.
 *
 * Presentational, and the viewer is a pure function of its inputs — no
 * internal selection state. `selectedKey` is the raw `?artifact` URL param;
 * {@link ChunkPage} owns writing it back when a nav row is picked (the
 * `board-selection`/`panel-selection` pattern), so a picked row, a reload,
 * and a shared link all resolve the same way. An absent key resolves to the
 * **most recent** entry; a key naming nothing in the store — a stale dock
 * link, or a garbage `?artifact` — resolves to the empty state
 * ({@link ArtifactPage} takes the same stance on a stale deep link) rather
 * than silently falling back to something else.
 *
 * The viewer composes {@link ChunkArtifactBody} in `full` mode — the single
 * owner of an artifact's rendering, the same component the dock's link rows
 * (`summary` mode) and the mobile single-artifact page compose.
 */
@Component({
  selector: 'app-chunk-artifacts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, FleetWhen, KitAsyncState],
  template: `
    <div class="art-tab" data-testid="chunk-artifacts-tab">
      <nav class="art-nav">
        @if (sortedArtifacts().length === 0) {
          <p class="none" data-testid="artifacts-tab-nav-empty">No artifacts yet.</p>
        } @else {
          <ul class="art-list" data-testid="artifacts-tab-nav">
            @for (art of sortedArtifacts(); track art.key) {
              <li>
                <button
                  type="button"
                  class="art-item"
                  [class.active]="art.key === effectiveKey()"
                  data-testid="artifacts-tab-nav-item"
                  [attr.data-artifact-key]="art.key"
                  (click)="pickArtifact.emit(art.key)"
                >
                  <span class="key">{{ art.key }}</span>
                  <span class="sub">
                    @if (art.recorded_at) {
                      <fleet-when [iso]="art.recorded_at" />
                    }
                    <span class="kind">{{ art.kind }}</span>
                  </span>
                </button>
              </li>
            }
          </ul>
        }
      </nav>
      <section class="art-view">
        <fleet-kit-async-state
          [state]="viewState()"
          [emptyText]="emptyMessage()"
          emptyTestid="artifacts-tab-empty"
        >
          @if (selectedArtifact(); as art) {
            <fleet-chunk-detail-artifact-body
              class="body"
              [artifact]="art"
              body="full"
              testid="artifacts-tab-artifact"
              data-testid="artifacts-tab-artifact"
            />
          }
        </fleet-kit-async-state>
      </section>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .art-tab {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
    }
    .art-nav {
      flex: none;
      display: flex;
      flex-direction: column;
      min-height: 0;
      max-height: 35%;
      border-bottom: 1px solid var(--line);
    }
    .none {
      margin: 0;
      padding: 0 8px 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .art-list {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .art-item {
      display: block;
      width: 100%;
      text-align: left;
      font-family: inherit;
      background: transparent;
      border: none;
      border-bottom: 1px solid var(--line);
      border-left: 2px solid transparent;
      color: var(--text);
      cursor: pointer;
      padding: 6px 8px;
    }
    .art-item:hover {
      background: var(--overlay-20);
    }
    /* The board dock's own "which one am I looking at" idiom (board-card.ts) —
       an accent border plus the shared selection tint. */
    .art-item.active {
      border-left-color: var(--cyan);
      background: var(--tint-selected);
    }
    .art-item .key {
      display: block;
      color: var(--cyan);
      font-size: var(--fs-xs);
      overflow-wrap: anywhere;
    }
    .art-item .sub {
      display: flex;
      gap: 8px;
      margin-top: 2px;
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--label-dim);
    }
    .art-item .sub .kind {
      margin-left: auto;
    }
    .art-view {
      position: relative;
      flex: 1;
      min-width: 0;
      min-height: 0;
      display: flex;
      flex-direction: column;
      background: var(--overlay-25);
    }
    .art-view .body {
      flex: 1;
      min-height: 0;
      padding: 8px;
    }
    @media (min-width: 720px) {
      .art-tab {
        flex-direction: row;
      }
      .art-nav {
        width: 300px;
        max-height: none;
        border-bottom: none;
        border-right: 1px solid var(--line);
      }
    }
  `,
})
export class ChunkArtifactsTab {
  /** The chunk's artifact store, server-ordered. */
  readonly artifacts = input.required<readonly ArtifactView[]>();

  /** The raw `?artifact` URL param — the requested selection, before this
   * component falls it back to the most recent entry. */
  readonly selectedKey = input<string | null>(null);

  /** Emitted with a nav row's key when the operator picks it. */
  readonly pickArtifact = output<string>();

  protected readonly sortedArtifacts = computed(() => sortArtifacts(this.artifacts()));

  /** {@link selectedKey}, defaulted to the most recent entry when absent. Stays
   * whatever `selectedKey` names when it names nothing in the store — that is
   * the dead-link case {@link viewState} resolves to `empty`, not a case to
   * paper over with a silent fallback. */
  protected readonly effectiveKey = computed<string | null>(() => {
    const key = this.selectedKey();
    if (key !== null) return key;
    const sorted = this.sortedArtifacts();
    return sorted.length > 0 ? sorted[sorted.length - 1].key : null;
  });

  protected readonly selectedArtifact = computed<ArtifactView | undefined>(() => {
    const key = this.effectiveKey();
    if (key === null) return undefined;
    return this.artifacts().find((art) => art.key === key);
  });

  protected readonly viewState = computed<KitAsyncStateValue>(() => {
    if (this.artifacts().length === 0) return 'empty';
    return this.selectedArtifact() === undefined ? 'empty' : 'ready';
  });

  protected readonly emptyMessage = computed(() => (this.artifacts().length === 0 ? 'No artifacts yet.' : 'NO SUCH ARTIFACT'));
}
