import { Listbox, Option } from '@angular/aria/listbox';
import { Directive, inject } from '@angular/core';
import { KitChipGroup } from './kit-chip-group';
/** Selectable chip whose wrapper owns focus. */
@Directive({ selector: 'fleet-kit-chip[fleetKitChipOption]', hostDirectives: [{ directive: Option, inputs: ['value', 'label', 'disabled'] }], host: { '(pointerdown)': 'onPointerDown()', '(click)': 'onClick()' } })
export class KitChipOptionDirective {
  private readonly option = inject(Option);
  private readonly group = inject(KitChipGroup);
  private readonly listbox = inject(Listbox);

  protected onPointerDown(): void { this.group.pointerDown(); }
  protected onClick(): void {
    if (!this.option.disabled() && !this.listbox.disabled()) this.group.pointerClick(this.option.value());
  }
}
