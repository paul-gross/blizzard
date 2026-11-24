import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { createCopyFlash } from '../../core/clipboard';
import { KitButton } from '../../kit/kit-button';

/**
 * An escalation's takeover commands, shown by the one rule every panel that renders an
 * escalation shares. Presentational only: it takes the two wire command fields and derives
 * everything else.
 *
 * `wrapped` is the `blizzard runner takeover` form the runner composes; it is primary
 * whenever present, with Copy, and `raw` demoted to a collapsed fallback below it. With no
 * wrapped command, `raw` is the primary copyable command under framing that does not tell
 * the operator to run it — the wire carries no discriminator between a runner-composed
 * resume command and the hub-authored guidance prose occupying the same field. With neither,
 * a no-command hint, never an empty command line.
 * Rules: `blizzard-context:/domain/humans/escalation.md` §The commands an escalation carries.
 *
 * The projected content leads the hint paragraph — the caller's own cause sentence.
 */
@Component({
  selector: 'fleet-chunk-detail-escalation-takeover',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './escalation-takeover.html',
  styleUrl: './escalation-takeover.css',
})
export class EscalationTakeover {
  /** The runner-composed `blizzard runner takeover` command; empty when absent. */
  readonly wrapped = input<string>('');
  /** The raw takeover field — a command or guidance prose; empty when absent. */
  readonly raw = input<string>('');

  private readonly copyFlash = createCopyFlash();
  protected readonly copied = this.copyFlash.copied;

  protected readonly hasWrapped = computed<boolean>(() => !!this.wrapped());
  protected readonly hasCommand = computed<boolean>(() => !!this.raw());

  /** The command the copy button and primary `<code>` carry: wrapped when present, else raw. */
  protected readonly primaryCommand = computed<string>(() => this.wrapped() || this.raw());

  protected copyTakeover(command: string): void {
    this.copyFlash.copy(command);
  }
}
