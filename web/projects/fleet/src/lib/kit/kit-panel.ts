import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The panel shell (issue #78) — the chrome every board and machine-panel
 * section duplicated: the bezeled panel body, the header row with an engraved
 * uppercase label and an optional count, and a scrolling body slot below it.
 * Presentational only, no query/mutation/client injection: it renders exactly
 * what it is handed.
 *
 * The header row also exposes a `[header]`-slotted content projection for a
 * consumer that needs more than one label in its header (e.g. a second `.lbl`
 * span, or a count that isn't a bare number) — `label`/`count` cover the
 * common case, the slot covers the rest.
 *
 * Two CSS custom-property hooks (`--kit-panel-bg`, `--kit-panel-header-bg`)
 * let a consumer whose panel chrome uses a different background — `fleet`'s
 * gradient panel vs. `local-panel`'s flat one — override it from outside
 * without forking this component; custom properties cascade through view
 * encapsulation, so a parent's own styles can set them on `<fleet-kit-panel>`.
 */
@Component({
  selector: 'fleet-kit-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="p-hdr">
      <span class="lbl">{{ label() }}</span>
      @if (hasCount()) {
        <span class="lbl" [attr.data-testid]="countTestid()">{{ count() }}</span>
      }
      <ng-content select="[header]" />
    </div>
    <div class="p-body">
      <ng-content />
    </div>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
      background: var(--kit-panel-bg, linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%));
      border: 1px solid var(--bezel);
    }
    .p-hdr {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--kit-panel-header-bg, var(--overlay-25));
      flex: none;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 var(--overlay-90);
    }
    .p-body {
      overflow-y: auto;
      overflow-x: hidden;
      flex: 1;
      min-height: 0;
    }
  `,
})
export class KitPanel {
  /** The header's engraved label — the panel's name. */
  readonly label = input.required<string>();

  /** An optional trailing header value (a count, or any short string); omitted
   * entirely (not rendered as `0` or empty) when `null`/`undefined`/`''`. */
  readonly count = input<number | string | null>(null);

  /** The count span's `data-testid`, or `null` for none — a consumer whose
   * existing testid the count span replaces names it here. */
  readonly countTestid = input<string | null>(null);

  protected hasCount(): boolean {
    const c = this.count();
    return c !== null && c !== undefined && c !== '';
  }
}
