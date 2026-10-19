import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { compactRef, formatAbsolute, formatLocalClockWithDay, injectNowSignal, type LocalClockWithDay } from 'fleet';

import type { PanelFact } from '../core/panel-reads.model';

/**
 * {@link FactLog}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the ledger row template — the
 * container keeps the query and the resolved async-state triad.
 */
@Component({
  selector: 'app-fact-log-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './fact-log-view.html',
  styleUrl: './fact-log-view.css',
})
export class FactLogView {
  /** The newest-first facts to render, each naming its runner. */
  readonly facts = input<readonly PanelFact[]>([]);

  /** The tick `clockInfo` reads against, so the day label rolls over at midnight. */
  private readonly now = injectNowSignal();

  protected ref(id: string): string {
    return compactRef(id);
  }

  /** The fact's browser-local clock time, plus day context when it's not from
   * today — the ledger reads as a tail -f, but an operator can be anywhere. */
  protected clockInfo(fact: PanelFact): LocalClockWithDay | null {
    return formatLocalClockWithDay(fact.created_at, new Date(this.now()));
  }

  /** {@link clockInfo}'s full local date + time, for the stamp's hover tooltip. */
  protected absolute(fact: PanelFact): string {
    return formatAbsolute(fact.created_at);
  }
}
