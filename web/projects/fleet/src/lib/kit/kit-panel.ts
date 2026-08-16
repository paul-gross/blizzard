import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * The panel shell (issue #78) — the chrome every board and machine-panel
 * section duplicated: the bezeled panel body, the header row with an engraved
 * uppercase label and an optional count, and a scrolling body slot below it.
 * Presentational only, no query/mutation/client injection: it renders exactly
 * what it is handed.
 *
 * The header row also exposes a `[header]`-slotted content projection, in two
 * declared modes rather than a CSS coincidence a consumer has to discover:
 * **supplement** (`label`/`count` set, `[header]` content alongside them) —
 * for a second `.lbl` span or a count that isn't a bare number, sized to its
 * own content like any other flex item; and **owns-the-bar** (`label`/`count`
 * both unset, {@link hasHeaderContent} `true`) — for a consumer replacing the
 * header row outright (e.g. {@link MachineDetailHeader}), whose projected root
 * the kit itself sizes to fill `.p-hdr`'s full width (`.hdr-slot`) so the
 * consumer never has to know `.p-hdr` is a flex row to size against it.
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
  template: `
    @if (label() || hasCount() || hasHeaderContent()) {
      <div class="p-hdr">
        @if (label()) {
          <span class="lbl" [style.color]="accent()">{{ label() }}</span>
        }
        @if (hasCount()) {
          <span class="lbl" [class.cnt-accent]="!!accent()" [attr.data-testid]="countTestid()">{{ count() }}</span>
        }
        <div class="hdr-slot" [class.hdr-slot--owned]="ownsBar()">
          <ng-content select="[header]" />
        </div>
      </div>
    }
    <div class="p-body" [class.p-body--noscroll]="!bodyScroll()">
      <ng-content />
    </div>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
      background: var(--kit-panel-bg, linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%));
      border: 1px solid var(--bezel);
    }
    .p-hdr {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--kit-panel-header-bg, var(--overlay-25));
      flex: none;
    }
    /* Transparent by default (the supplement mode's small trailing spans size
       to their own content, unaffected) — .hdr-slot--owned is the only mode
       that turns this into a real, positioned flex item, so a consumer never
       opts into stretching by accident. */
    .hdr-slot {
      display: contents;
    }
    .hdr-slot--owned {
      display: block;
      flex: 1;
      min-width: 0;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 var(--overlay-90);
    }
    /* Only set when accent is present — the mock's count reads bright
       against a colored label, never the muted engraved-label grey. */
    .lbl.cnt-accent {
      color: var(--snow);
    }
    /* Positioned so {@link KitAsyncState}'s absolutely-centered status line
       resolves against *this* panel's body when the consumer provides no
       nearer positioned ancestor of its own — without it the line escapes to
       whatever ancestor happens to be positioned (the initial containing
       block, when none is) and paints over unrelated content. */
    .p-body {
      position: relative;
      overflow-y: auto;
      overflow-x: hidden;
      flex: 1;
      min-height: 0;
    }
    /* bodyScroll(false): this panel's content owns its own scrolling, so
       .p-body only clips — a second auto scrollbar here is exactly the bug
       (issue #309): it grabs the drag instead of the content's own scroller. */
    .p-body--noscroll {
      overflow: hidden;
    }
  `,
})
export class KitPanel {
  /** The header's engraved label — the panel's name. `null`/`''` (a consumer
   * whose header is entirely `[header]`-slotted, e.g. the runner dock
   * projecting {@link MachineDetailHeader}'s own bar in whole) renders no
   * `.lbl` span at all, rather than an empty one sitting beside the slot's
   * content — but see {@link hasHeaderContent}, without which an empty
   * `label` alone renders no header bar at all. */
  readonly label = input<string | null>(null);

  /** An optional trailing header value (a count, or any short string); omitted
   * entirely (not rendered as `0` or empty) when `null`/`undefined`/`''`. */
  readonly count = input<number | string | null>(null);

  /** Whether this render actually projects something into the `[header]`
   * slot right now — `false` (the default) is every existing consumer's
   * behavior, unaffected. A consumer that (like {@link MachineDetail})
   * conditionally projects its own header content sets this to that same
   * condition, so `.p-hdr` renders only while there is something in it —
   * `label`/`count` alone already gate correctly and never need this.
   * Combined with an unset `label`/`count`, this also switches the slot into
   * owns-the-bar mode (see the class docstring). */
  readonly hasHeaderContent = input(false);

  /** True when the `[header]` slot owns the whole bar rather than
   * supplementing a `label`/`count` — see the class docstring. */
  protected readonly ownsBar = computed(() => this.hasHeaderContent() && !this.label() && !this.hasCount());

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
