import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, input, signal } from '@angular/core';

/**
 * The quiet overflow menu (mobile polish feedback item 5, `../../../docs/designs/mobile/core-flows.html`)
 * — a small icon trigger that reveals its projected content in a floating
 * panel, closing on an outside click or `Escape`. Built for burying
 * {@link ViewportToggle} in a titlebar corner instead of leaving it always
 * visible: every titlebar (`hub`'s desktop nav and mobile titlebar, the
 * runner's local-panel header) wraps the same toggle in one of these rather
 * than forking its own popover.
 *
 * No CDK overlay: the codebase has no existing overlay/menu idiom to match
 * (`bzh:frontend-kit`'s "match whatever menu idiom exists" — there is none
 * yet), so this stays a minimal, token-styled, absolutely-positioned panel
 * rather than reaching for a heavier dependency for one popover.
 */
@Component({
  selector: 'fleet-kit-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      class="trigger"
      [attr.aria-label]="ariaLabel()"
      [attr.aria-expanded]="open()"
      [attr.data-testid]="testid()"
      (click)="toggle()"
    >
      ⋮
    </button>
    @if (open()) {
      <div class="panel" [attr.data-testid]="panelTestid()">
        <ng-content />
      </div>
    }
  `,
  styles: `
    :host {
      display: inline-flex;
      position: relative;
    }
    .trigger {
      font-family: inherit;
      background: none;
      border: 1px solid transparent;
      color: var(--label);
      cursor: pointer;
      line-height: 1;
      padding: 3px 8px;
      border-radius: 3px;
    }
    .trigger:hover {
      color: var(--cyan);
      border-color: var(--line);
    }
    .trigger[aria-expanded='true'] {
      color: var(--cyan);
      background: var(--overlay-25);
    }
    .panel {
      position: absolute;
      top: 100%;
      right: 0;
      margin-top: 4px;
      z-index: 20;
      background: var(--panel);
      border: 1px solid var(--bezel);
      box-shadow: 0 8px 20px var(--overlay-40);
      padding: 8px 10px;
      white-space: nowrap;
    }
  `,
  host: {
    '(document:click)': 'onDocumentClick($event)',
    '(keydown.escape)': 'close()',
  },
})
export class KitMenu {
  private readonly elementRef = inject(ElementRef<HTMLElement>);

  /** The trigger button's accessible name. */
  readonly ariaLabel = input('Menu');

  /** The trigger button's `data-testid`, or `null` for none. The panel's own
   * testid (when open) is derived from it (`${testid}-panel`) rather than a
   * second input, so every instance's panel testid stays distinct without a
   * caller having to spell it out twice. */
  readonly testid = input<string | null>(null);

  protected readonly open = signal(false);

  protected readonly panelTestid = computed(() => {
    const testid = this.testid();
    return testid ? `${testid}-panel` : null;
  });

  protected toggle(): void {
    this.open.update((value) => !value);
  }

  protected close(): void {
    this.open.set(false);
  }

  /** Closes on any click outside this component's own host — a click on the
   * trigger or anywhere in the projected panel content is still inside the
   * host element, so `contains` keeps it open; only a click elsewhere in the
   * document closes it. */
  protected onDocumentClick(event: MouseEvent): void {
    if (!this.open()) return;
    if (!this.elementRef.nativeElement.contains(event.target as Node)) this.close();
  }
}
