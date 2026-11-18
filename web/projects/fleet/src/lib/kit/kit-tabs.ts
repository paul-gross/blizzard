import { Tab, TabList } from '@angular/aria/tabs';
import {
  afterNextRender,
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  inject,
  Injector,
  input,
  model,
  untracked,
  viewChild,
  viewChildren,
} from '@angular/core';

import { KitTab, KitTabStrip, type KitTabVariant } from './kit-tab';

/** One tab in a {@link KitTabs} strip. */
export interface KitTabOption {
  readonly value: string;
  readonly label: string;
  /** How many the tab holds, shown after its label; none when absent. */
  readonly count?: number | null;
  /** Optional per-tab test hook, forwarded to the rendered tab button. */
  readonly testid?: string;
}

/**
 * The tab strip — a row of tabs selecting between sibling views
 * of the same page (e.g. a chunk detail page's General/Artifacts split).
 * Shaped like {@link KitChips} (options in, `[(active)]` two-way), rendered with
 * the same {@link KitTabStrip}/{@link KitTab} chrome the main nav's routed
 * tabs wear — one tab treatment, not two.
 *
 * Aria's `ngTabList`/`ngTab` own the roles, the single Tab stop, Left/Right/Home/End
 * movement and selection that follows focus. The panels the tabs switch between are
 * {@link KitTabPanel}s inside the consumer's {@link KitTabGroup}, not this component.
 *
 * The consumer's `active` value stays authoritative: it is pushed into Aria whenever it or
 * the set of tabs changes (so a tab that appears later is selected when it arrives), and
 * only a person's click or key press writes it back — Aria's own default-state writes never do.
 */
@Component({
  selector: 'fleet-kit-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, TabList, Tab],
  templateUrl: './kit-tabs.html',
  styleUrl: './kit-tabs.css',
})
export class KitTabs {
  readonly options = input.required<readonly KitTabOption[]>();
  /** The selected tab's `value`; two-way, written back only by a person's click or key press. */
  readonly active = model<string | null>(null);
  /** `sub` for a strip nested inside a page beneath the app's top strip. */
  readonly variant = input<KitTabVariant>('top');

  private readonly list = viewChild(TabList);
  private readonly tabs = viewChildren(Tab);
  /** True from a click or key press on the strip until Aria has published its selection. */
  private userInput = false;

  constructor() {
    const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
    const injector = inject(Injector);
    // Aria moves its selection inside the click/key handler but publishes it to `selectedTab`
    // only in its next render's write phase, so a person's input is a window that closes after
    // that render — the read phase, which runs once every write-phase effect has.
    const begin = () => {
      this.userInput = true;
      afterNextRender({ read: () => (this.userInput = false) }, { injector });
    };
    for (const type of ['click', 'keydown']) host.addEventListener(type, begin, true);
    inject(DestroyRef).onDestroy(() => {
      for (const type of ['click', 'keydown']) host.removeEventListener(type, begin, true);
    });

    afterRenderEffect({
      write: () => {
        const list = this.list();
        this.tabs();
        const selected = list?.selectedTab();
        const want = this.active() ?? undefined;
        if (list === undefined) return;
        untracked(() => {
          if (selected !== want) list.selectedTab.set(want);
          // Aria moves its roving Tab stop only on keys and focus, so a selection that came from
          // outside (a card's jump, the browser's Back) would leave the stop on the old tab.
          const target = list.findTab(want)?._pattern;
          if (target !== undefined && list._pattern.inputs.activeItem() !== target) list._pattern.inputs.activeItem.set(target);
        });
      },
    });
  }

  protected onSelected(value: string | undefined): void {
    if (this.userInput && value !== undefined && value !== this.active()) this.active.set(value);
  }
}
