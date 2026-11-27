import { Listbox } from '@angular/aria/listbox';
import { afterRenderEffect, DestroyRef, Directive, ElementRef, inject, input, output, untracked, type OnInit } from '@angular/core';

import { trackUserInput } from './user-input-window';

/**
 * A master list — a vertical Aria listbox of {@link KitSelectRow}s with one Tab stop. Up, Down,
 * Home, End and type-ahead move the highlight (and focus) without opening anything; Enter, Space or a
 * click opens the row the person acted on, through `valueChange`.
 *
 * The consumer's `value` stays authoritative: it is pushed into Aria whenever it or the set of rows
 * changes, and only a person's click or key press writes it back — Aria's own writes (pruning the
 * values of rows that have not registered yet, its default state) never do. Pressing the row that is
 * already open emits it again and keeps the consumer's value, so a re-open is possible.
 *
 * Every list binds `selectionMode="explicit" [softDisabled]="false" [wrap]="false"`: Aria's defaults
 * would open rows as the highlight moves, let disabled rows take it, and wrap it past the ends. A host
 * directive's input cannot be defaulted by the kit, so the consumer's template carries the bindings;
 * a dev build throws when one is missing. Orientation (vertical) and single selection are Aria's defaults.
 */
@Directive({
  selector: '[fleetKitSelectList]',
  exportAs: 'fleetKitSelectList',
  hostDirectives: [{ directive: Listbox, inputs: ['selectionMode', 'softDisabled', 'wrap', 'focusMode'] }],
  host: { '[attr.aria-label]': 'ariaLabel()' },
})
export class KitSelectList<V = unknown> implements OnInit {
  /** The open row's `value`; `null` when none is. */
  readonly value = input<V | null>(null);
  readonly valueChange = output<V>();
  readonly ariaLabel = input<string | null>(null);

  private readonly listbox = inject<Listbox<V>>(Listbox);
  /** True from a click or key press on the list until Aria has published its selection. */
  private readonly userInput = trackUserInput();
  private lastPushed: V | null | undefined;
  private readonly element = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private hadFocus = false;
  private focusedValue: V | undefined;

  constructor() {
    const host = this.element;
    // Focus on a row that a re-render destroys falls to <body> with no focusout, so remember that
    // focus was inside the list and which row held it, and hand it back to the same value.
    const focusIn = () => {
      this.hadFocus = true;
      this.focusedValue = this.activeValue();
    };
    const focusOut = (event: FocusEvent) => {
      if (host.contains(event.relatedTarget as Node | null)) return;
      this.hadFocus = false;
      // Focus left the list: a selection that changed while it was inside deferred the roving stop's
      // move, so put the stop on the selected row now (no focus, no scroll).
      const selected = this.value();
      if (selected !== null) this.moveStop(selected);
    };
    host.addEventListener('focusin', focusIn);
    host.addEventListener('focusout', focusOut);
    inject(DestroyRef).onDestroy(() => {
      host.removeEventListener('focusin', focusIn);
      host.removeEventListener('focusout', focusOut);
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
        this.rowCount();
        untracked(() => {
          this.push(want);
          this.restoreFocus();
          // Aria leaves an empty list tabbable; an empty list owns no Tab stop.
          if (this.rowCount() === 0) host.removeAttribute('tabindex');
        });
      },
    });
  }

  ngOnInit(): void {
    if (typeof ngDevMode === 'undefined' || ngDevMode) {
      const missing = [
        this.listbox.selectionMode() === 'follow' && 'selectionMode="explicit"',
        this.listbox.softDisabled() && '[softDisabled]="false"',
        this.listbox.wrap() && '[wrap]="false"',
      ].filter((binding) => binding !== false);
      if (missing.length > 0) throw new Error(`fleetKitSelectList is missing ${missing.join(' ')}`);
    }
  }

  /** Give focus back to the row that held it when a re-render destroyed that row's element. */
  private restoreFocus(): void {
    if (!this.hadFocus || this.focusedValue === undefined) return;
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    this.moveStop(this.focusedValue)?.element()?.focus({ preventScroll: true });
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
    // While focus is inside the list the roving stop follows focus, not the selection.
    if (this.userInput() || this.element.contains(document.activeElement)) return;
    // Aria moves its roving Tab stop only on keys and focus (its public `gotoIndex` focuses the
    // row), so a selection that came from outside (a route refresh, Back, a deep link) would leave
    // the stop on another row. Set the active item directly: no focus, no scroll.
    this.moveStop(want);
  }

  private onAriaValue(aria: readonly V[]): void {
    if (!this.userInput()) return;
    const consumer = this.value();
    const acted =
      aria.length === 1
        ? aria[0]
        : this.activeValue();
    if (acted === undefined || (aria.length === 1 && acted === consumer))
      return;
    // The person opened `acted` (a toggle on the row that is already open deselects it, and still
    // opens it again). The consumer decides what is selected, so snap Aria back to its value.
    this.valueChange.emit(acted);
    const restore = consumer === null ? [] : [consumer];
    if (aria.length !== restore.length || aria[0] !== restore[0])
      this.listbox.value.set(restore);
  }

  // Aria exposes no public way to read the highlighted row, count rows, or move the roving stop
  // without focusing; these three helpers are the only reads of its `_pattern`, so an Aria upgrade
  // has one place to fix.
  private activeValue(): V | undefined {
    return this.listbox._pattern.inputs.activeItem()?.value();
  }

  /** How many rows have registered; reading it in an effect re-runs the effect as rows come and go. */
  private rowCount(): number {
    return this.listbox._pattern.inputs.items().length;
  }

  /** Put the roving Tab stop on the row valued `value`, without focus or scroll; the row, if any. */
  private moveStop(value: V) {
    const inputs = this.listbox._pattern.inputs;
    const row = inputs.items().find((item) => item.value() === value);
    if (row !== undefined) inputs.activeItem.set(row);
    return row;
  }
}
