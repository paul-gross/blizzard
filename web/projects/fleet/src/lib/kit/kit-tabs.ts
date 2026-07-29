import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/** One tab in a {@link KitTabs} strip. */
export interface KitTabOption {
  readonly value: string;
  readonly label: string;
  /** Optional per-tab test hook, forwarded to the rendered tab button. */
  readonly testid?: string;
}

/**
 * The tab strip (issue #160) — a small, bordered, bottom-open row of tabs
 * selecting between sibling views of the same page (e.g. a chunk detail
 * page's General/Artifacts split). `kit/` had no tabs primitive before this;
 * shaped like {@link KitChips} (options + selected value in, `(choose)` out)
 * but rendered as the mockup's square, bottom-open strip, its active tab
 * reading as an extension of the panel body below it rather than a pill.
 *
 * `role="tablist"` / `role="tab"` / `aria-selected` so the strip is
 * navigable by assistive tech; this component does not itself own the
 * panel the tabs switch between — a consumer's own `@switch` does that.
 */
@Component({
  selector: 'fleet-kit-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="tabs" role="tablist">
      @for (option of options(); track option.value) {
        <button
          type="button"
          class="tab"
          role="tab"
          [class.active]="option.value === activeValue()"
          [attr.aria-selected]="option.value === activeValue()"
          [attr.data-testid]="option.testid ?? null"
          (click)="choose.emit(option.value)"
        >
          {{ option.label }}
        </button>
      }
    </div>
  `,
  styles: `
    :host {
      display: contents;
    }
    .tabs {
      display: flex;
      gap: 2px;
    }
    .tab {
      font-family: inherit;
      background: transparent;
      border: 1px solid var(--line);
      border-bottom: none;
      color: var(--label);
      cursor: pointer;
      font-size: var(--fs-xs);
      letter-spacing: 0.12em;
      text-transform: uppercase;
      padding: 5px 18px;
    }
    .tab:hover {
      color: var(--text);
    }
    .tab.active {
      color: var(--cyan);
      border-color: var(--bezel-hi);
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
    }
  `,
})
export class KitTabs {
  readonly options = input.required<readonly KitTabOption[]>();
  readonly activeValue = input<string | null>(null);

  /** Emits the clicked option's `value`. */
  readonly choose = output<string>();
}
