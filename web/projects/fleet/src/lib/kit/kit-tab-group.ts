import { Directive } from '@angular/core';
import { Tabs } from '@angular/aria/tabs';

/**
 * The scope a tab strip and the panels it switches share — put on the consumer's
 * nearest element enclosing both the {@link KitTabs} strip and its {@link KitTabPanel}s.
 * Carries Aria's `ngTabs`, which pairs each tab with its panel by value; no other kit
 * part carries it, so the strip and the panels always register with one group.
 *
 * A directive, not a wrapper element: the strip and the panels often live in different
 * slots of a layout (a panel header and its body, a page shell's tab slot and default
 * slot), so the group sits on whatever element already encloses both.
 */
@Directive({
  selector: '[fleetKitTabGroup]',
  hostDirectives: [Tabs],
})
export class KitTabGroup {}
