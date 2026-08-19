import { ChangeDetectionStrategy, Component, Directive, computed, contentChild, input } from '@angular/core';

/**
 * Marks the element a consumer projects into {@link KitPanel}'s header slot —
 * the `fleetKitPanelHeader` attribute is both the projection selector and this
 * directive's own. It carries no behavior: it exists so the panel can *observe*
 * its slot occupancy through a content query rather than being told about it by
 * a second, hand-maintained input the consumer has to keep in sync with what it
 * actually projects. Import it alongside `KitPanel` wherever a
 * `fleetKitPanelHeader` element is projected.
 */
@Directive({ selector: '[fleetKitPanelHeader]' })
export class KitPanelHeader {}

/**
 * The panel shell (issue #78) — the chrome every board and machine-panel
 * section duplicated: the bezeled panel body, the header row with an engraved
 * uppercase label and an optional count, and a scrolling body slot below it.
 * Presentational only, no query/mutation/client injection: it renders exactly
 * what it is handed.
 *
 * The header row also exposes a `fleetKitPanelHeader`-slotted content projection,
 * in two declared modes rather than a CSS coincidence a consumer has to discover:
 * **supplement** (`label`/`count` set, slotted content alongside them) —
 * for a second `.lbl` span or a count that isn't a bare number, sized to its
 * own content like any other flex item; and **owns-the-bar** (`label`/`count`
 * both unset, something projected into the slot) — for a consumer replacing
 * the header row outright (e.g. {@link MachineDetailHeader}), whose projected
 * root the kit itself sizes to fill `.p-hdr`'s full width (`.hdr-slot`) so the
 * consumer never has to know `.p-hdr` is a flex row to size against it.
 *
 * Which mode is live is *observed*, not declared: a {@link KitPanelHeader}
 * content query answers whether anything is in the slot right now, so a panel
 * with an unset label and an unfilled slot renders no header bar at all, and a
 * consumer projecting conditionally has one thing to get right (the
 * `fleetKitPanelHeader` attribute) instead of two that can drift apart.
 *
 * Two CSS custom-property hooks (`--kit-panel-bg`, `--kit-panel-header-bg`)
 * let a consumer whose panel chrome uses a different background — `fleet`'s
 * gradient panel vs. `local-panel`'s flat one — override it from outside
 * without forking this component; custom properties cascade through view
 * encapsulation, so a parent's own styles can set them on `<fleet-kit-panel>`.
 *
 * An optional `accent` input colors the label (and switches the count to the
 * mock's bright `--snow`, mock screen C's "Needs you"/"In motion"/"Done today"
 * headers, `../../../hub/src/app/board/glance/glance-view.ts`) — `null`
 * (the default) leaves both exactly as every existing consumer already
 * renders them, so this is additive, not a restyle.
 *
 * `bodyScroll` (default `true`, today's behavior) gates whether `.p-body`
 * itself scrolls. The runners, asks, and event log rails leave it at the
 * default — a single scrolling body is right for them. The board panel
 * (issue #309) sets it `false`: its content manages its own per-lane
 * scrolling internally, and a second scroll container one level up is the
 * bug, not a feature — `.p-body` instead clips to the panel's height so its
 * content can resolve a real height to lay out against.
 */
@Component({
  selector: 'fleet-kit-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-panel.html',
  styleUrl: './kit-panel.css',
})
export class KitPanel {
  /** The header's engraved label — the panel's name. `null`/`''` (a consumer
   * whose header is entirely slotted, e.g. the runner dock projecting
   * {@link MachineDetailHeader}'s own bar in whole) renders no `.lbl` span at
   * all, rather than an empty one sitting beside the slot's content. With no
   * `count` and an unfilled slot either, an empty `label` renders no header bar
   * at all. */
  readonly label = input<string | null>(null);

  /** An optional trailing header value (a count, or any short string); omitted
   * entirely (not rendered as `0` or empty) when `null`/`undefined`/`''`. */
  readonly count = input<number | string | null>(null);

  /** Whatever is in the header slot on this render, or `undefined` — the kit's
   * own read of its slot occupancy, so `.p-hdr` renders only while there is
   * something to put in it. A consumer that (like {@link MachineDetail})
   * projects its header conditionally needs no second declaration of that
   * condition; it marks the projected element `fleetKitPanelHeader` and imports
   * {@link KitPanelHeader}. */
  protected readonly headerContent = contentChild(KitPanelHeader, { descendants: true });

  /** True when the header slot owns the whole bar rather than
   * supplementing a `label`/`count` — see the class docstring. */
  protected readonly ownsBar = computed(() => !!this.headerContent() && !this.label() && !this.hasCount());

  /** The count span's `data-testid`, or `null` for none — a consumer whose
   * existing testid the count span replaces names it here. */
  readonly countTestid = input<string | null>(null);

  /** A design-token color (e.g. `'var(--red)'`) the label resolves to instead
   * of the default `--label` grey, and that flips the count span to
   * `--snow` — `null` (the default) is the panel's existing look, unchanged. */
  readonly accent = input<string | null>(null);

  /** Whether `.p-body` scrolls itself — `true` (the default) is every existing
   * consumer's current behavior; a panel whose projected content manages its
   * own scrolling sets this `false` instead. */
  readonly bodyScroll = input(true);

  protected hasCount(): boolean {
    const c = this.count();
    return c !== null && c !== undefined && c !== '';
  }
}
