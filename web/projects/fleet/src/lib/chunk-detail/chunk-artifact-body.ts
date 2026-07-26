import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ArtifactView } from '../api/hub';
import { formatWhen } from '../when';

/**
 * One artifact, rendered — the head (key, recency, kind) over a kind-dependent
 * body: an **asset**'s content verbatim, a **git_commit**'s pinned `repo @ commit`
 * with its branch link.
 *
 * The single owner of that rendering. Two shells show an artifact —
 * {@link ChunkArtifacts} stacks every entry of the store inline in the desktop
 * dock, and the hub's mobile shell opens one per page behind a link — and both
 * compose this rather than re-typing the kind branch, so a new field or a third
 * `kind` lands once (`canon:one-owner`).
 *
 * `testid` roots every handle this component renders, the same convention
 * {@link MobileTitlebar} uses, so two mounts never collide on one
 * `data-testid` (`bzh:frontend-kit`'s globally-unique handle rule). It defaults
 * to `artifact` — the desktop dock's existing handles — so that side needs no
 * input to keep its specs passing.
 *
 * Laid out as a flex column with the asset body taking the free space: in the
 * dock's auto-height list item that resolves to the content's own height
 * (unchanged), and in a height-capped page it gives the body the scroll region
 * a long findings text needs.
 */
@Component({
  selector: 'fleet-chunk-detail-artifact-body',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="a-head">
      <span class="a-key" [attr.data-testid]="keyTestid()">{{ artifact().key }}</span>
      @if (when(); as w) {
        <span class="a-when" [attr.data-testid]="whenTestid()" [attr.title]="artifact().recorded_at">{{ w }}</span>
      }
      <span class="a-kind">{{ artifact().kind }}</span>
    </div>
    @if (artifact().kind === 'asset') {
      <pre class="a-content" [attr.data-testid]="contentTestid()">{{ artifact().content }}</pre>
    } @else {
      <div class="a-ref" [attr.data-testid]="refTestid()">
        <span class="a-repo">{{ artifact().repo }}</span>
        @if (artifact().branch_name) {
          <span class="a-sep">·</span>
          @if (artifact().branch_url) {
            <a
              class="a-branch"
              [attr.data-testid]="branchTestid()"
              [href]="artifact().branch_url"
              target="_blank"
              rel="noreferrer"
              [attr.title]="artifact().branch_url"
              >{{ artifact().branch_name }}</a
            >
          } @else {
            <span class="a-branch" [attr.data-testid]="branchTestid()">{{ artifact().branch_name }}</span>
          }
        }
        <span class="a-commit">&#64; {{ artifact().commit_hash }}</span>
      </div>
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    /* Key left; the recency stamp and kind cluster right (.a-when's auto margin). */
    .a-head {
      flex: none;
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
      overflow-wrap: anywhere;
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
      flex: 1;
      min-height: 0;
      overflow: auto;
      margin: 4px 0 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--overlay-30);
      color: var(--text);
      font-size: var(--fs-sm);
    }
    .a-ref {
      flex: none;
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
export class ChunkArtifactBody {
  /** The artifact to render. */
  readonly artifact = input.required<ArtifactView>();

  /** The root every handle this component renders derives from. Defaults to the
   * desktop dock's existing `artifact-*` handles. */
  readonly testid = input('artifact');

  protected readonly keyTestid = computed(() => `${this.testid()}-key`);
  protected readonly whenTestid = computed(() => `${this.testid()}-when`);
  protected readonly contentTestid = computed(() => `${this.testid()}-content`);
  protected readonly refTestid = computed(() => `${this.testid()}-ref`);
  protected readonly branchTestid = computed(() => `${this.testid()}-branch`);

  /** The attachment instant, pre-formatted, or `null` when the entry carries none. */
  protected readonly when = computed(() => {
    const at = this.artifact().recorded_at;
    return at ? formatWhen(at) : null;
  });
}
