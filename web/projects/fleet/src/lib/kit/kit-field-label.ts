import { InjectionToken } from '@angular/core';

/**
 * The label an enclosing {@link KitField} offers its projected controls: the id of its label element. A control with no
 * `ariaLabel` of its own names itself through it (`aria-labelledby`). Optional — a control outside a field injects
 * nothing. Kit-internal; not exported from a kit entry.
 */
export interface KitFieldLabel {
  readonly labelId: string;
}

export const KIT_FIELD_LABEL = new InjectionToken<KitFieldLabel>(
  'KIT_FIELD_LABEL',
);
