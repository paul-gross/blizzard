import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The graph detail's **lifecycle status** section — the action-error line
 * (report-don't-swallow pattern) and the entry-node line. Ordinary
 * body content below `fleet-kit-panel`'s header bar; the retire/re-enable control
 * itself lives in `GraphDetailHeader`, and a failed attempt reports here.
 *
 * Presentational only: forwards its two inputs straight to the template
 * (`bzh:frontend-container-presentational`); it holds no state of its own.
 */
@Component({
  selector: 'app-graph-detail-lifecycle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-detail-lifecycle.html',
  styleUrl: './graph-detail-lifecycle.css',
})
export class GraphDetailLifecycle {
  /** Set on a failed retire/enable (report-don't-swallow pattern), or
   * `null` between attempts. */
  readonly actionError = input<string | null>(null);

  readonly entryNodeName = input.required<string>();
}
