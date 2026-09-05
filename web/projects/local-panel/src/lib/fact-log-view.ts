import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { compactRef, formatAbsolute, formatLocalClockWithDay, type LocalClockWithDay, type runnerApi } from 'fleet';

/**
 * {@link FactLog}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the ledger row template — the
 * container keeps the query and the resolved async-state triad.
 */
@Component({
  selector: 'local-fact-log-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './fact-log-view.html',
  styleUrl: './fact-log-view.css',
})
export class FactLogView {
  /** The newest-first facts to render, resolved by the container. */
  readonly facts = input<readonly runnerApi.FactView[]>([]);

  protected ref(id: string): string {
    return compactRef(id);
  }

  /** The fact's browser-local clock time, plus day context when it's not from
   * today — the ledger reads as a tail -f, but an operator can be anywhere. */
  protected clockInfo(fact: runnerApi.FactView): LocalClockWithDay | null {
    return formatLocalClockWithDay(fact.created_at);
  }

  /** {@link clockInfo}'s full local date + time, for the stamp's hover tooltip
   * (issue #175). */
  protected absolute(fact: runnerApi.FactView): string {
    return formatAbsolute(fact.created_at);
  }
}
