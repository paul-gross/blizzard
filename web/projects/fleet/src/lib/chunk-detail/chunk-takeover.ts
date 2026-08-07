import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import type { ChunkDetail, EscalationView } from '../api/hub';
import { KitButton } from '../kit/kit-button';

/**
 * The chunk's open escalation, needing a human takeover. Presentational only: it
 * holds the `detail` input and derives everything else
 * (`bzh:frontend-container-presentational`) — no query, no store access.
 *
 * `wrapped_takeover_command` is the `blizzard runner takeover` form the runner
 * composes; it is primary whenever present, with the raw `takeover_command`
 * demoted to a collapsed fallback disclosure below it. When wrapped is absent but
 * raw is present, the raw field renders as the primary copyable command instead.
 * Wrapped-vs-raw rules and the wire field's own optionality:
 * `blizzard-context:/domain/humans.md` §Escalation and `EscalationView` in
 * `src/blizzard/wire/chunk.py`. A hub-authored escalation's guidance prose also
 * lands here and copies as text, which is harmless.
 *
 * "Takeover" in the class/selector name is the deliberate operator-facing word for
 * entering a parked session — matching `blizzard runner takeover` and the "Needs
 * human · takeover" tag below — not the separately-modeled Takeover entity (its own
 * store table, `TakeoverService`, `OpenTakeoverView`, `GET /api/takeovers`; see
 * `blizzard-context:/domain/humans.md`'s §Takeover). This component renders none of
 * that entity's state: it only reads the open escalation (`data-testid="escalation"`).
 */
@Component({
  selector: 'fleet-chunk-detail-takeover',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  template: `
    @if (escalation(); as esc) {
      <div class="escalation" data-testid="escalation">
        <div class="s-head"><span class="tag">Needs human · takeover</span></div>
        @if (hasWrapped()) {
          <p class="esc-hint">
            The worker escalated (epoch {{ esc.epoch }}). Run as the runner's service account (the socket is
            owner-only): this command runs from any directory on the runner's host and records the takeover first,
            so the fleet will not respawn or judge the session while it is open:
          </p>
          <div class="takeover">
            <code class="cmd" data-testid="takeover-command">{{ primaryCommand() }}</code>
            <fleet-kit-button testid="copy-takeover" (click)="copyTakeover(primaryCommand())">
              {{ copied() ? 'Copied' : 'Copy' }}
            </fleet-kit-button>
          </div>
          <details class="raw-fallback" data-testid="takeover-command-raw-fallback">
            <summary>Unwrapped fallback — cds directly into the &lt;workdir&gt;</summary>
            <code class="cmd">{{ esc.takeover_command }}</code>
          </details>
        } @else if (hasCommand()) {
          <p class="esc-hint">
            The worker escalated (epoch {{ esc.epoch }}). Run the takeover command to enter its session:
          </p>
          <div class="takeover">
            <code class="cmd" data-testid="takeover-command">{{ primaryCommand() }}</code>
            <fleet-kit-button testid="copy-takeover" (click)="copyTakeover(primaryCommand())">
              {{ copied() ? 'Copied' : 'Copy' }}
            </fleet-kit-button>
          </div>
        } @else {
          <p class="esc-hint" data-testid="no-command-hint">
            The worker escalated (epoch {{ esc.epoch }}). There is no composed command for this escalation.
            <code>blizzard runner takeover</code> may still reach the parked session directly; if it refuses,
            resolve it on the chunk (see its bounce history) and requeue.
          </p>
        }
      </div>
    }
  `,
  styles: `
    :host {
      display: contents;
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
    .escalation {
      border: 1px solid var(--red-dim);
      background: color-mix(in srgb, var(--red) 6%, transparent);
      padding: 6px;
      margin-bottom: 8px;
    }
    .esc-hint {
      margin: 0 0 6px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .takeover {
      display: flex;
      gap: 4px;
      align-items: stretch;
    }
    .takeover .cmd {
      flex: 1;
      min-width: 0;
      overflow-x: auto;
      white-space: pre;
      background: var(--overlay-40);
      border: 1px solid var(--line);
      color: var(--amber-hi);
      padding: 4px 6px;
      font-size: var(--fs-sm);
    }
    .raw-fallback {
      margin-top: 6px;
    }
    .raw-fallback summary {
      cursor: pointer;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      list-style: none;
    }
    .raw-fallback summary::-webkit-details-marker {
      display: none;
    }
    .raw-fallback .cmd {
      display: block;
      margin-top: 4px;
      overflow-x: auto;
      white-space: pre;
      background: var(--overlay-40);
      border: 1px solid var(--line);
      color: var(--text);
      padding: 4px 6px;
      font-size: var(--fs-sm);
    }
  `,
})
export class ChunkTakeover {
  /** The chunk aggregate to render the open escalation of, if any. */
  readonly detail = input.required<ChunkDetail>();

  /** Transient "Copied" state for the takeover-command copy button. */
  protected readonly copied = signal(false);

  /** The chunk's open escalation, if it currently needs a human takeover. */
  protected readonly escalation = computed<EscalationView | null>(() => this.detail().escalation ?? null);

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
