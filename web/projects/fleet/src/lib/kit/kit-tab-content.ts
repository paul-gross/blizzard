import { Directive } from '@angular/core';
import { TabContent } from '@angular/aria/tabs';

/**
 * A {@link KitTabPanel}'s body, as `<ng-template fleetKitTabContent>` inside the panel.
 * Carries Aria's `ngTabContent`: the body is created when its panel becomes visible and
 * destroyed when it hides, so a hidden panel's components (and the reads they make) do not
 * exist — the behaviour a consumer's `@switch` on the active value gave.
 */
@Directive({
  selector: 'ng-template[fleetKitTabContent]',
  hostDirectives: [TabContent],
})
export class KitTabContent {}
