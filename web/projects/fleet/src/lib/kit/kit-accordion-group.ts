import { Directive } from '@angular/core';
import { AccordionGroup } from '@angular/aria/accordion';

/**
 * The scope a set of {@link KitAccordionSection}s share — put on the consumer's nearest
 * element enclosing all of them. Carries Aria's `ngAccordionGroup`, which moves between
 * headers with Up/Down/Home/End (every header stays a Tab stop), and lets each section's trigger
 * register with it; a section outside a group does not render.
 *
 * A directive, not a wrapper element, so the group sits on whatever element already holds the
 * sections. Aria's defaults stand: more than one section may be open, and a disabled header is
 * still reached by the arrow keys but does not toggle.
 */
@Directive({
  selector: '[fleetKitAccordionGroup]',
  hostDirectives: [
    {
      directive: AccordionGroup,
      inputs: ['disabled', 'multiExpandable', 'softDisabled', 'wrap'],
    },
  ],
})
export class KitAccordionGroup {}
