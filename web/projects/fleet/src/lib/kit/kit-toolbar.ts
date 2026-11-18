import { Toolbar, ToolbarWidget } from '@angular/aria/toolbar';
import { Directive, inject, output } from '@angular/core';

/** Groups action widgets into a single keyboard navigation stop. */
@Directive({
  selector: '[fleetKitToolbar]',
  hostDirectives: [{ directive: Toolbar, inputs: ['disabled', 'orientation'] }],
})
export class KitToolbar {}

/** Action inside a {@link KitToolbar}. Handle actions only through `invoked`. */
@Directive({
  selector: '[fleetKitToolbarWidget]',
  hostDirectives: [{ directive: ToolbarWidget, inputs: ['disabled'] }],
  host: {
    role: 'button',
    '(click)': 'activate()',
    '(keydown.enter)': 'onKey($event)',
    '(keydown.space)': 'onKey($event)',
  },
})
export class KitToolbarWidget {
  private readonly widget = inject(ToolbarWidget);
  private readonly toolbar = inject(Toolbar);
  readonly invoked = output<void>();

  protected activate(): void {
    if (!this.widget.disabled() && !this.toolbar.disabled()) this.invoked.emit();
  }

  protected onKey(event: Event): void {
    event.preventDefault();
    if (!(event as KeyboardEvent).repeat) this.activate();
  }
}
