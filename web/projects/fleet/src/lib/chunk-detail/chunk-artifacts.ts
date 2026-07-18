import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ArtifactView, ChunkDetail } from '../api/hub';
import { formatWhen } from '../when';

/** An artifact-store entry plus its pre-formatted attachment recency stamp. */
type StampedArtifact = ArtifactView & { readonly when: string };

/**
 * The chunk's artifact store (issue #79) — each entry keyed
 * `{node}.{artifact-name}.{epoch}`, with an **asset's** findings text inline
 * and a **git_commit's** pinned `repo @ commit` reference. Presentational only.
 */
@Component({
  selector: 'fleet-chunk-detail-artifacts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="arts">
      <div class="s-head"><span class="tag">Artifacts</span></div>
      @if (artifacts().length === 0) {
        <p class="none" data-testid="artifacts-empty">No artifacts yet.</p>
      } @else {
        <ul class="artifacts" data-testid="artifacts">
          @for (art of artifacts(); track art.key) {
            <li class="artifact" data-testid="artifact" [attr.data-kind]="art.kind">
              <div class="a-head">
                <span class="a-key" data-testid="artifact-key">{{ art.key }}</span>
                @if (art.when) {
                  <span class="a-when" data-testid="artifact-when" [attr.title]="art.recorded_at">{{ art.when }}</span>
                }
                <span class="a-kind">{{ art.kind }}</span>
              </div>
              @if (art.kind === 'asset') {
                <pre class="a-content" data-testid="artifact-content">{{ art.content }}</pre>
              } @else {
                <div class="a-ref" data-testid="artifact-ref">
                  <span class="a-repo">{{ art.repo }}</span>
                  @if (art.branch_name) {
                    <span class="a-sep">·</span>
                    @if (art.branch_url) {
                      <a
                        class="a-branch"
                        data-testid="artifact-branch"
                        [href]="art.branch_url"
                        target="_blank"
                        rel="noreferrer"
                        [attr.title]="art.branch_url"
                        >{{ art.branch_name }}</a
                      >
                    } @else {
                      <span class="a-branch" data-testid="artifact-branch">{{ art.branch_name }}</span>
                    }
                  }
                  <span class="a-commit">&#64; {{ art.commit_hash }}</span>
                </div>
              }
            </li>
          }
        </ul>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .arts {
      margin-bottom: 8px;
    }
    .s-head {
      margin-bottom: 6px;
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .artifacts {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .artifact {
      border: 1px solid var(--line);
      background: var(--overlay-20);
      padding: 4px 5px;
    }
    /* Key left; the recency stamp and kind cluster right (.a-when's auto margin). */
    .a-head {
      display: flex;
      align-items: baseline;
      gap: 6px;
    }
    .a-head .a-kind {
      margin-left: auto;
    }
    .a-head .a-when + .a-kind {
      margin-left: 0;
    }
    .a-key {
      color: var(--cyan);
      font-size: var(--fs-xs);
    }
    .a-kind {
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    /* When the artifact was attached (ULID-decoded) — recency, dim like the kind. */
    .a-when {
      color: var(--label-dim);
      font-size: var(--fs-label);
      white-space: nowrap;
      margin-left: auto;
    }
    .a-content {
      margin: 4px 0 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--overlay-30);
      color: var(--text);
      font-size: var(--fs-sm);
    }
    .a-ref {
      margin-top: 4px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 4px;
    }
    .a-branch {
      color: var(--amber-hi);
    }
    a.a-branch {
      text-decoration: none;
    }
    a.a-branch:hover,
    a.a-branch:focus-visible {
      text-decoration: underline;
      outline: none;
    }
    .a-sep {
      color: var(--label-dim);
    }
  `,
})
export class ChunkArtifacts {
  /** The chunk aggregate to render (its attached artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The artifact store, oldest attachment first — ordered by `recorded_at` (the
   * ULID-decoded attachment instant), with each entry's recency stamp pre-formatted.
   * Entries without a stamp keep the server's store-key order among themselves. */
  protected readonly artifacts = computed<readonly StampedArtifact[]>(() =>
    [...(this.detail().artifacts ?? [])]
      .sort((a, b) => (a.recorded_at ?? '').localeCompare(b.recorded_at ?? ''))
      .map((art) => ({ ...art, when: art.recorded_at ? formatWhen(art.recorded_at) : '' })),
  );
}
