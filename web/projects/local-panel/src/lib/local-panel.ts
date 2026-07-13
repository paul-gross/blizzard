import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The runner's machine-local panel shell — the runner app's own view, added on
 * top of the shared fleet views (D-097). Minimal by design: a header and an
 * empty-state body. The local surface (held environments, agent slots, open
 * asks, escalations — design/cli.md `blizzard runner status`) lands on this
 * shell as those features arrive. Color comes from the shared design-token
 * layer (`fleet` library, design/tokens.css), never hard-coded hex.
 */
@Component({
  selector: 'fleet-local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="lp" data-testid="local-panel">
      <header class="lp-header">
        <div class="brand">
          blizzard<small>runner · local panel</small>
        </div>
        <div class="spacer"></div>
        <div class="conn" data-testid="conn">
          <span class="lbl">Runner</span>
          <span class="v">{{ connection() }}</span>
        </div>
      </header>
      <section class="body">
        <p class="empty" data-testid="empty-state">NO ACTIVITY — RUNNER IDLE</p>
      </section>
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: var(--mono);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .lp {
      display: flex;
      flex-direction: column;
      height: 100%;
    }
    .lbl {
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .lp-header {
      flex: none;
      display: flex;
      align-items: stretch;
      height: 40px;
      border-bottom: 1px solid var(--bezel);
      background: linear-gradient(180deg, #0d1526, #080d18);
    }
    .brand {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: 15px;
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .brand small {
      color: var(--label);
      font-size: 9px;
      letter-spacing: 0.18em;
    }
    .spacer {
      flex: 1;
      border-right: 1px solid var(--line);
    }
    .conn {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
    }
    .conn .v {
      color: var(--cyan);
      font-size: 15px;
    }
    .body {
      flex: 1;
      position: relative;
    }
    .empty {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      color: var(--label-dim);
      font-size: 11px;
      letter-spacing: 0.12em;
    }
  `,
})
export class LocalPanel {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');
}
