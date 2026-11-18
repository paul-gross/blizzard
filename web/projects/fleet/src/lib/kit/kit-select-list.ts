import { Listbox } from '@angular/aria/listbox';
import { SIGNAL, signalSetFn, type SignalNode } from '@angular/core/primitives/signals';
import {
  afterNextRender,
  afterRenderEffect,
  DestroyRef,
  Directive,
  ElementRef,
  inject,
  Injector,
  input,
  output,
  untracked,
} from '@angular/core';

/**
 * A master list — a vertical Aria listbox of {@link KitSelectRow}s with one Tab stop. Up, Down,
 * Home, End and type-ahead move the highlight (and focus) without opening anything; Enter, Space or a
 * click opens the row the person acted on, through `valueChange`.
 *
 * The consumer's `value` stays authoritative: it is pushed into Aria whenever it or the set of rows
 * changes, and only a person's click or key press writes it back — Aria's own writes (pruning the
 * values of rows that have not registered yet, its default state) never do. Pressing the row that is
 * already open emits it again and keeps the consumer's value, so a re-open is possible.
 */
@Directive({
  selector: '[fleetKitSelectList]',
  exportAs: 'fleetKitSelectList',
  hostDirectives: [Listbox],
  host: { '[attr.aria-label]': 'ariaLabel()' },
})
export class KitSelectList<V = unknown> {
  /** The open row's `value`; `null` when none is. */
  readonly value = input<V | null>(null);
  readonly valueChange = output<V>();
  readonly ariaLabel = input<string | null>(null);

  private readonly listbox = inject<Listbox<V>>(Listbox);
  /** True from a click or key press on the list until Aria has published its selection. */
  private userInput = false;
  private lastPushed: V | null | undefined;

  constructor() {
    // A host directive's inputs cannot be bound from the host's own metadata, so the list's fixed
    // Aria settings are written onto the input signals directly, once, before anything reads them.
    // `orientation` (vertical), single selection and `roving` focus are already Aria's defaults.
    const fix = <T>(input: () => T, value: T) => signalSetFn((input as unknown as { [SIGNAL]: SignalNode<T> })[SIGNAL], value);
    fix(this.listbox.selectionMode, 'explicit');
    fix(this.listbox.softDisabled, false);
    fix(this.listbox.wrap, false);

    const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
    const injector = inject(Injector);
    // Aria moves its selection inside the click/key handler but publishes it to `value` in its next
    // render's write phase, so a person's input is a window that closes after that render — the read
    // phase, which runs once every write-phase effect has.
    const begin = () => {
      this.userInput = true;
      afterNextRender({ read: () => (this.userInput = false) }, { injector });
    };
    for (const type of ['click', 'keydown']) host.addEventListener(type, begin, true);
    inject(DestroyRef).onDestroy(() => {
      for (const type of ['click', 'keydown']) host.removeEventListener(type, begin, true);
    });

    // Aria writes `value` itself (pruning, explicit toggles); read it through its model's changes.
    afterRenderEffect({
      read: () => {
        const aria = this.listbox.value();
        untracked(() => this.onAriaValue(aria));
      },
    });

    afterRenderEffect({
      write: () => {
        const want = this.value();
        this.rowsVersion();
        untracked(() => this.push(want));
      },
    });
  }

  private rowsVersion(): unknown {
    return this.listbox._pattern.inputs.items();
  }

  /** Push the consumer's value into Aria, and move the roving stop to it when it changed outside. */
  private push(want: V | null): void {
    const aria = this.listbox.value();
    const wanted = want === null ? [] : [want];
    if (aria.length !== wanted.length || aria[0] !== wanted[0]) this.listbox.value.set(wanted);
    if (want === null || this.lastPushed === want) {
      this.lastPushed = want;
      return;
    }
    this.lastPushed = want;
    if (this.userInput) return;
    // Aria moves its roving Tab stop only on keys and focus (its public `gotoIndex` focuses the
    // row), so a selection that came from outside (a route refresh, Back, a deep link) would leave
    // the stop on another row. Set the active item directly: no focus, no scroll.
    const target = this.listbox._pattern.inputs.items().find((item) => item.value() === want);
    if (target !== undefined) this.listbox._pattern.inputs.activeItem.set(target);
  }

  private onAriaValue(aria: readonly V[]): void {
    if (!this.userInput) return;
    const consumer = this.value();
    const acted = this.listbox._pattern.inputs.activeItem()?.value();
    if (aria.length === 1 && aria[0] !== consumer) {
      this.valueChange.emit(aria[0]);
    } else if (aria.length === 0 && consumer !== null && acted === consumer) {
      // Aria's explicit toggle deselected the open row: re-open it and keep the value.
      this.valueChange.emit(consumer);
      this.listbox.value.set([consumer]);
    }
  }
}
