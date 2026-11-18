import { Option } from '@angular/aria/listbox';
import { Directive } from '@angular/core';
/** Selectable chip whose wrapper owns focus. */
@Directive({ selector: 'fleet-kit-chip[fleetKitChipOption]', hostDirectives: [{ directive: Option, inputs: ['value', 'label', 'disabled'] }] })
export class KitChipOptionDirective {}
