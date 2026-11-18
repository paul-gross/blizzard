import { Directive, ElementRef, Injector, afterNextRender, computed, effect, inject, untracked } from '@angular/core';
import { GRID, GRID_CELL, GridCellWidget } from '@angular/aria/grid';

/**
 * The native control inside a {@link KitGridCell} — put on `<fleet-kit-button>` or `<fleet-kit-select>`.
 * Carries Aria's `ngGridCellWidget` on the component's wrapper and points Aria's `focusTarget` at the
 * component's native button (`gridFocusTarget`), so arrow navigation lands focus on the real button.
 * Exported as `fleetKitGridWidget`; the control binds `controlTabIndex()` back to its own
 * `[controlTabIndex]`.
 *
 * - `gridWidgetType="simple"` (a button): the native Enter or Space click is the activation; the bridge
 *   never synthesizes another.
 * - `gridWidgetType="complex"` (a select): Enter activates through Aria; Space is handled here, since Aria
 *   activates complex widgets on Enter only. The control's `(opened)` and `(closed)` call
 *   {@link activate} and {@link deactivate}, so a pointer open or close keeps the widget in step.
 *
 * Tab: Aria pins a widget-bearing cell and its wrapper to `tabindex="-1"` and does not manage the
 * focus target's, so {@link controlTabIndex} is the cell's only Tab stop — `0` for the active cell's
 * enabled control in an enabled grid, `-1` otherwise. It reads only public Aria state.
 *
 * A control disabled while its cell is active — a row's change in flight — loses focus to the browser and the active
 * cell to Aria. When the control enables again the widget takes focus back (unless the user moved focus outside the
 * grid meanwhile), which also makes its cell active again.
 *
 * While a complex widget's popup is open, focus is in an overlay outside the grid. Aria deactivates a
 * widget whenever focus leaves its host for elsewhere, which would close that popup, so a focus move
 * into the popup the control owns (`aria-controls`) is not let reach Aria.
 */
@Directive({
  selector: '[fleetKitGridWidget]',
  exportAs: 'fleetKitGridWidget',
  hostDirectives: [
    {
      directive: GridCellWidget,
      inputs: ['focusTarget: gridFocusTarget', 'widgetType: gridWidgetType', 'disabled: gridDisabled'],
      outputs: ['activated: gridActivated', 'deactivated: gridDeactivated'],
    },
  ],
  host: {
    ngGridCellWidget: '',
    '(keydown.space)': 'onSpace($event)',
    '(keyup.space)': 'onSpaceUp($event)',
    '(focusout)': 'onFocusOut($event)',
  },
})
export class KitGridWidget {
  private readonly widget = inject(GridCellWidget);
  private readonly cell = inject(GRID_CELL);
  private readonly grid = inject(GRID);
  private readonly injector = inject(Injector);
  private activeWhenEnabled = false;
  private wasDisabled = false;

  constructor() {
    effect(() => {
      const disabled = this.widget.disabled();
      const active = this.cell.active();
      untracked(() => {
        if (disabled) {
          this.wasDisabled = true;
          return;
        }
        if (this.wasDisabled && this.activeWhenEnabled && this.focusIsAdrift()) {
          afterNextRender(() => this.control()?.focus(), { injector: this.injector });
        }
        this.wasDisabled = false;
        this.activeWhenEnabled = active;
      });
    });
  }

  /** Focus is nowhere, or on the grid's own fallback cell — not on something the user moved to outside the grid. */
  private focusIsAdrift(): boolean {
    const focused = document.activeElement;
    return !focused || focused === document.body || this.widget.element.closest('[role="grid"]')?.contains(focused) === true;
  }

  private control(): HTMLElement | undefined {
    const target = this.widget.focusTarget();
    const resolved = typeof target === 'function' ? target(this.widget.element) : target;
    return (resolved instanceof ElementRef ? resolved.nativeElement : resolved) ?? undefined;
  }

  /** The `tabindex` for the control's native button: `0` only for the active cell's enabled control. */
  readonly controlTabIndex = computed(() =>
    this.cell.active() && !this.grid.disabled() && !this.widget.disabled() ? 0 : -1,
  );

  /** Activates the widget — a no-op while already activated, disabled, or `simple`. */
  activate(): void {
    this.widget.activate();
  }

  /** Deactivates the widget — a no-op while not activated. */
  deactivate(): void {
    this.widget.deactivate();
  }

  protected onSpace(event: Event): void {
    if (!this.opensOnSpace(event)) return;
    event.preventDefault();
    this.widget.activate();
  }

  /** The native click of a Space press lands on keyup; suppress it so it cannot toggle the popup shut. */
  protected onSpaceUp(event: Event): void {
    if (this.opensOnSpace(event)) event.preventDefault();
  }

  protected onFocusOut(event: FocusEvent): void {
    const next = event.relatedTarget;
    const control = event.target;
    if (!(next instanceof Node) || !(control instanceof HTMLButtonElement)) return;
    const popupId = control.getAttribute('aria-controls');
    if (popupId && document.getElementById(popupId)?.contains(next)) event.stopPropagation();
  }

  /** A Space press on a complex widget's native button — the one Aria does not activate. */
  private opensOnSpace(event: Event): boolean {
    return (
      this.widget.widgetType() === 'complex' && !this.widget.disabled() && event.target instanceof HTMLButtonElement
    );
  }
}
