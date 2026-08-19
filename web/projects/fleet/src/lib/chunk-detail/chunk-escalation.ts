import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import type { ChunkDetail, ChunkEscalationView } from '../api/hub';
import { KitButton } from '../kit/kit-button';

/**
 * The chunk's open escalation, needing a human takeover. Presentational only: it
 * holds the `detail` input and derives everything else
 * (`bzh:frontend-container-presentational`) — no query, no store access.
 *
 * `wrapped_takeover_command` is the `blizzard runner takeover` form the runner
 * composes; it is primary whenever present, with the raw `takeover_command`
 * demoted to a collapsed fallback disclosure below it. When wrapped is absent but
 * raw is present, the raw field renders as the primary copyable command instead —
 * under framing that does not tell the operator to run it, because the wire carries
 * no discriminator between a runner-composed resume command and the hub-authored
 * guidance prose that occupies the same field.
 * Wrapped-vs-raw rules and the wire field's own optionality:
 * `blizzard-context:/domain/humans.md` §Escalation and `ChunkEscalationView` in
 * `src/blizzard/wire/chunk.py`.
 */
@Component({
  selector: 'fleet-chunk-detail-escalation',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './chunk-escalation.html',
  styleUrl: './chunk-escalation.css',
})
export class ChunkEscalation {
  /** The chunk aggregate to render the open escalation of, if any. */
  readonly detail = input.required<ChunkDetail>();

  /** Transient "Copied" state for the takeover-command copy button. */
  protected readonly copied = signal(false);

  /** The chunk's open escalation, if it currently needs a human takeover. */
  protected readonly escalation = computed<ChunkEscalationView | null>(() => this.detail().escalation ?? null);

  /** Whether the escalation carries a runner-composed wrapped command — the
   * primary form once present. */
  protected readonly hasWrapped = computed<boolean>(() => !!this.escalation()?.wrapped_takeover_command);

  /** Whether the escalation carries the raw field — evaluated once `hasWrapped`
   * above is false, distinguishing a raw-only escalation (render it as the primary
   * copyable command) from a genuinely empty one (render neither). See the class doc
   * for what each shape means. */
  protected readonly hasCommand = computed<boolean>(() => !!this.escalation()?.takeover_command);

  /** The command the copy button and primary `<code>` carry: the wrapped
   * `blizzard runner takeover` form when present, else the raw field — the fallback
   * the wire contract promises. Both operands are reachable: the wrapped branch and
   * the raw-only branch each render this. */
  protected readonly primaryCommand = computed<string>(() => {
    const esc = this.escalation();
    if (!esc) return '';
    return esc.wrapped_takeover_command || esc.takeover_command;
  });

  /** Copy the primary takeover command to the clipboard, flashing "Copied" when it lands. */
  protected copyTakeover(command: string): void {
    const clipboard = globalThis.navigator?.clipboard;
    if (!clipboard) return;
    void clipboard.writeText(command).then(() => {
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 1500);
    });
  }
}
