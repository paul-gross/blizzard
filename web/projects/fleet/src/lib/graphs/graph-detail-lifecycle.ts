import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The graph detail's **lifecycle status** section — the action-error line
 * (issue #42's report-don't-swallow pattern) and the entry-node line. Ordinary
 * body content below `fleet-kit-panel`'s header bar, where `GraphDetailHeader`
 * (the identity supplement — lifecycle text, graph id, and the retire/re-enable
 * control itself) lives instead: the retire/re-enable confirm-then-emit pair
 * moved there (alongside the lifecycle text it right-aligns against) so a
 * failed attempt still reports here, right below where the control lives.
 *
 * Presentational only: forwards its two inputs straight to the template
 * (`bzh:frontend-container-presentational`); it holds no state of its own.
 */
@Component({
  selector: 'fleet-graph-detail-lifecycle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-detail-lifecycle.html',
  styleUrl: './graph-detail-lifecycle.css',
})
export class GraphDetailLifecycle {
  /** Set on a failed retire/enable (issue #42's report-don't-swallow pattern), or
   * `null` between attempts. */
  readonly actionError = input<string | null>(null);

  readonly entryNodeName = input.required<string>();
}
