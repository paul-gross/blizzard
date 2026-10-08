import { ChangeDetectionStrategy, Component, Directive, computed, contentChild, input } from '@angular/core';

/**
 * Marks the element projected into {@link KitPanel}'s header slot, so the panel
 * observes slot occupancy through a content query instead of a second input that
 * could drift from what is projected. Carries no behavior.
 */
@Directive({ selector: '[fleetKitPanelHeader]' })
export class KitPanelHeader {}

/**
 * The panel shell: a bezeled body, a header row with an engraved uppercase label
 * and an optional count, and a body slot below it. Presentational only.
 *
 * The `fleetKitPanelHeader` slot has two modes. **Supplement**: `label` or `count`
 * is set, and slotted content sits alongside them at its own size. **Owns the
 * bar**: both are unset and something is projected, and the projected root is
 * sized to fill `.p-hdr`'s full width (`.hdr-slot`). Which mode is live is
 * observed through the {@link KitPanelHeader} content query; with no label, count,
 * or slotted content, no header bar renders.
 *
 * `--kit-panel-bg` and `--kit-panel-header-bg` override the panel's backgrounds
 * from outside; custom properties cascade through view encapsulation.
 *
 * With `bodyScroll` false, `.p-body` clips to the panel's height instead of
 * scrolling, so projected content that scrolls itself can resolve a real height.
 */
@Component({
  selector: 'fleet-kit-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-panel.html',
  styleUrl: './kit-panel.css',
})
export class KitPanel {
  /** The header's engraved label — the panel's name. `null`/`''` renders no
   * `.lbl` span at all, rather than an empty one beside the slot's content. */
  readonly label = input<string | null>(null);

  /** An optional trailing header value (a count, or any short string); omitted
   * entirely (not rendered as `0` or empty) when `null`/`undefined`/`''`. */
  readonly count = input<number | string | null>(null);

  /** Whatever is in the header slot on this render, or `undefined`. */
  protected readonly headerContent = contentChild(KitPanelHeader, { descendants: true });

  /** True when the header slot owns the whole bar rather than
   * supplementing a `label`/`count` — see the class docstring. */
  protected readonly ownsBar = computed(() => !!this.headerContent() && !this.label() && !this.hasCount());

  /** The count span's `data-testid`, or `null` for none. */
  readonly countTestid = input<string | null>(null);

  /** A design-token color (e.g. `'var(--red)'`) the label resolves to instead
   * of the default `--label` grey, and that flips the count span to
   * `--snow` — `null` (the default) keeps the default label and count colors. */
  readonly accent = input<string | null>(null);

  /** Whether `.p-body` scrolls itself; `false` for projected content that manages its own scrolling. */
  readonly bodyScroll = input(true);

  protected hasCount(): boolean {
    const c = this.count();
    return c !== null && c !== undefined && c !== '';
  }
}
