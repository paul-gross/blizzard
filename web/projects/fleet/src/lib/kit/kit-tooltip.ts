import {
  ChangeDetectionStrategy,
  Component,
  Directive,
  ElementRef,
  Injector,
  OnDestroy,
  computed,
  inject,
  input,
  inputBinding,
  signal,
} from '@angular/core';
import { ConnectedPosition, Overlay, OverlayRef } from '@angular/cdk/overlay';
import { ComponentPortal } from '@angular/cdk/portal';

/** Above the host by default, flipping below when the top position would not fit —
 * `FlexibleConnectedPositionStrategy` walks the list in order and uses the first
 * position whose overlay box actually fits the viewport. */
const POSITIONS: ConnectedPosition[] = [
  { originX: 'center', originY: 'top', overlayX: 'center', overlayY: 'bottom', offsetY: -8 },
  { originX: 'center', originY: 'bottom', overlayX: 'center', overlayY: 'top', offsetY: 8 },
];

let nextPanelId = 0;

/**
 * The floating panel a {@link KitTooltip} trigger opens in a CDK overlay — a bare,
 * token-styled surface holding the described text. Presentational only: it renders
 * exactly the string and id it is handed, and never decides for itself when it is
 * shown or hidden.
 */
@Component({
  selector: 'fleet-kit-tooltip-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'tooltip',
    '[attr.id]': 'panelId()',
  },
  templateUrl: './kit-tooltip.html',
  styleUrl: './kit-tooltip.css',
})
class KitTooltipPanel {
  /** The descriptive text the panel displays. */
  readonly text = input.required<string>();

  /** The panel's own DOM id — the trigger's `aria-describedby` target. */
  readonly panelId = input.required<string>();
}

/**
 * A tooltip trigger (issue #405) — applied directly on the element it describes
 * (`[fleetTooltip]`, not a wrapping component), it opens a small token-styled panel
 * in a CDK overlay on hover or keyboard focus and closes it on the inverse
 * (mouseleave/blur) or `Escape`. The described text is a plain string input rather
 * than a `TemplateRef`: unlike {@link KitMenu}, a trigger directive introduces no
 * projection boundary for a content query to fail to cross, so there is nothing a
 * template would buy over a string.
 *
 * `aria-describedby` is a host binding this directive owns outright: it mints the
 * panel's id itself (a simple unique-id counter) and binds it only while the panel
 * is actually open, so a consumer wires the relationship by importing this directive
 * alone, with nothing of its own to keep in sync.
 *
 * The workspace's first direct `@angular/cdk/overlay` consumer — `@angular/cdk` is
 * already a dependency via {@link KitMenu} (`@angular/cdk/menu`) and `KitDialog`
 * (`@angular/cdk/a11y`), so no new package is needed. Positioning is a minimal
 * `flexibleConnectedTo` strategy: centred above the host by default, flipping below
 * when the panel would not fit there.
 */
@Directive({
  selector: '[fleetTooltip]',
  host: {
    '[attr.aria-describedby]': 'describedBy()',
    '(mouseenter)': 'show()',
    '(mouseleave)': 'hide()',
    '(focus)': 'show()',
    '(blur)': 'hide()',
    '(keydown.escape)': 'hide()',
  },
})
export class KitTooltip implements OnDestroy {
  private readonly elementRef = inject(ElementRef<HTMLElement>);
  private readonly overlay = inject(Overlay);
  private readonly injector = inject(Injector);

  private readonly panelId = `fleet-kit-tooltip-${nextPanelId++}`;
  private readonly isOpen = signal(false);
  private overlayRef: OverlayRef | null = null;

  /** The descriptive text the annotated element is described by. */
  readonly fleetTooltip = input.required<string>();

  /** The panel's id while open, so `aria-describedby` resolves to a real element;
   * `null` while closed, so nothing points at a panel that isn't there. */
  protected readonly describedBy = computed(() => (this.isOpen() ? this.panelId : null));

  protected show(): void {
    if (this.overlayRef?.hasAttached()) return;

    this.overlayRef = this.overlay.create({
      positionStrategy: this.overlay.position().flexibleConnectedTo(this.elementRef).withPositions(POSITIONS),
      scrollStrategy: this.overlay.scrollStrategies.reposition(),
    });
    // Bound in, not set after attach: attach() itself change-detects the new
    // component, so a post-attach setInput would run too late for its required inputs.
    this.overlayRef.attach(
      new ComponentPortal(KitTooltipPanel, null, this.injector, null, [
        inputBinding('text', () => this.fleetTooltip()),
        inputBinding('panelId', () => this.panelId),
      ]),
    );
    this.isOpen.set(true);
  }

  protected hide(): void {
    this.overlayRef?.dispose();
    this.overlayRef = null;
    this.isOpen.set(false);
  }

  ngOnDestroy(): void {
    this.hide();
  }
}
