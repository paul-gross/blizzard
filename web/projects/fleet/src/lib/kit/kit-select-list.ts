import { Listbox } from '@angular/aria/listbox';
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
  signal,
  untracked,
  type OnInit,
} from '@angular/core';

import type { KitSelectRow } from './kit-select-row';
import { trackUserInput } from './user-input-window';

/** The keys Aria moves the highlight on; any other single character is type-ahead. */
const MOVE_KEYS = ['ArrowUp', 'ArrowDown', 'Home', 'End'];

/**
 * A master list — a vertical Aria listbox of {@link KitSelectRow}s with one Tab stop. DOM focus stays on
 * the list; Up, Down, Home, End and type-ahead move the highlight (`aria-activedescendant`, scrolled into
 * view) without opening anything; Enter, Space or a click opens the row the person acted on, through
 * `valueChange`. A row that receives focus takes the highlight and hands focus to the list; a focusable
 * element inside a row keeps its own until a key that moves the highlight is pressed there, which takes
 * focus to the list. The highlighted row, not the list, draws the focus indicator.
 *
 * The consumer's `value` stays authoritative: it is pushed into Aria whenever it or the set of rows
 * changes, and only a person's click or key press writes it back — Aria's own writes (pruning the
 * values of rows that have not registered yet, its default state) never do. Pressing the row that is
 * already open emits it again and keeps the consumer's value, so a re-open is possible.
 *
 * Every list binds `selectionMode="explicit" [softDisabled]="false" [wrap]="false"
 * focusMode="activedescendant"`: Aria's defaults would open rows as the highlight moves, let disabled
 * rows take it, wrap it past the ends and move DOM focus from row to row. A host directive's input cannot
 * be defaulted by the kit, so the consumer's template carries the bindings; a dev build throws when one is
 * missing. Orientation (vertical) and single selection are Aria's defaults.
 */
@Directive({
  selector: '[fleetKitSelectList]',
  exportAs: 'fleetKitSelectList',
  hostDirectives: [{ directive: Listbox, inputs: ['selectionMode', 'softDisabled', 'wrap', 'focusMode'] }],
  host: {
    '[attr.aria-label]': 'ariaLabel()',
    style: 'outline: none',
  },
})
export class KitSelectList<V = unknown> implements OnInit {
  /** The open row's `value`; `null` when none is. */
  readonly value = input<V | null>(null);
  readonly valueChange = output<V>();
  readonly ariaLabel = input<string | null>(null);

  private readonly listbox = inject<Listbox<V>>(Listbox);
  /** The rows Aria holds, in no particular order: each registers itself, so one declared in a child
   * component's template counts too, which a content query would miss. */
  private readonly rows = signal<readonly KitSelectRow[]>([]);
  /** True from a click or key press on the list until Aria has published its selection. */
  private readonly userInput = trackUserInput();
  private lastPushed: V | null | undefined;
  private readonly element = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly injector = inject(Injector);

  constructor() {
    const host = this.element;
    const focusIn = (event: FocusEvent) => {
      const row = this.rows().find((r) => r.element === event.target);
      if (row !== undefined) {
        this.moveStop(row);
        host.focus({ preventScroll: true });
        return;
      }
      // Keyboard focus entering the list shows the highlighted row; a pointer press never scrolls.
      const entered = event.target === host && !host.contains(event.relatedTarget as Node | null);
      if (entered && host.matches(':focus-visible')) this.listbox.scrollActiveItemIntoView();
    };
    const focusOut = (event: FocusEvent) => {
      if (host.contains(event.relatedTarget as Node | null)) return;
      // Focus left the list: a selection that changed while it was inside deferred the highlight's
      // move, so put it on the selected row now (no focus, no scroll).
      const selected = this.value();
      if (selected !== null) this.moveStopTo(selected);
    };
    const keyDown = (event: KeyboardEvent) => {
      const typed = event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey;
      const arrowed = MOVE_KEYS.includes(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey;
      if (!typed && !arrowed) return;
      // Pressed on an element inside a row: take focus, so the highlight the key moves is drawn.
      if (event.target !== host) host.focus({ preventScroll: true });
      // After the render, so Aria's own key handler has moved the highlight.
      afterNextRender({ read: () => this.listbox.scrollActiveItemIntoView() }, { injector: this.injector });
    };
    host.addEventListener('focusin', focusIn);
    host.addEventListener('focusout', focusOut);
    host.addEventListener('keydown', keyDown);
    inject(DestroyRef).onDestroy(() => {
      host.removeEventListener('focusin', focusIn);
      host.removeEventListener('focusout', focusOut);
      host.removeEventListener('keydown', keyDown);
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
        const count = this.rows().length;
        untracked(() => {
          this.push(want);
          // Aria leaves an empty list tabbable, and its binding does not change when rows return, so the
          // attribute is both removed and put back here: an empty list owns no Tab stop.
          if (count === 0) host.removeAttribute('tabindex');
          else if (!host.hasAttribute('tabindex')) host.setAttribute('tabindex', '0');
        });
      },
    });
  }

  /** For {@link KitSelectRow} alone: it registers once its inputs are set and until it is destroyed. */
  register(row: KitSelectRow): () => void {
    this.rows.update((rows) => [...rows, row]);
    return () => this.rows.update((rows) => rows.filter((other) => other !== row));
  }

  ngOnInit(): void {
    if (typeof ngDevMode === 'undefined' || ngDevMode) {
      const missing = [
        this.listbox.selectionMode() === 'follow' && 'selectionMode="explicit"',
        this.listbox.softDisabled() && '[softDisabled]="false"',
        this.listbox.wrap() && '[wrap]="false"',
        this.listbox.focusMode() === 'roving' && 'focusMode="activedescendant"',
      ].filter((binding) => binding !== false);
      if (missing.length > 0) throw new Error(`fleetKitSelectList is missing ${missing.join(' ')}`);
    }
  }

  /** Push the consumer's value into Aria, and move the highlight to it when it changed outside. */
  private push(want: V | null): void {
    const aria = this.listbox.value();
    const wanted = want === null ? [] : [want];
    if (aria.length !== wanted.length || aria[0] !== wanted[0]) this.listbox.value.set(wanted);
    if (want === null || this.lastPushed === want) {
      this.lastPushed = want;
      return;
    }
    this.lastPushed = want;
    // While focus is inside the list the highlight is the person's, not the selection's.
    if (this.userInput() || this.element.contains(document.activeElement)) return;
    // Aria moves its highlight only on keys, clicks and focus, so a selection that came from outside
    // (a route refresh, Back, a deep link) would leave it on another row.
    this.moveStopTo(want);
  }

  private onAriaValue(aria: readonly V[]): void {
    if (!this.userInput()) return;
    const consumer = this.value();
    const acted = aria.length === 1 ? aria[0] : this.rows().find((row) => row.active())?.value();
    if (acted === undefined || (aria.length === 1 && acted === consumer)) return;
    // The person opened `acted` (a toggle on the row that is already open deselects it, and still
    // opens it again). The consumer decides what is selected, so snap Aria back to its value.
    this.valueChange.emit(acted as V);
    const restore = consumer === null ? [] : [consumer];
    if (aria.length !== restore.length || aria[0] !== restore[0]) this.listbox.value.set(restore);
  }

  private moveStopTo(value: V): void {
    const row = this.rows().find((r) => r.value() === value);
    if (row !== undefined) this.moveStop(row);
  }

  /** Move the highlight, and with it the list's stop, to `row`: no focus (in `activedescendant`
   * mode `gotoIndex` takes none), no scroll. A disabled row refuses it. */
  private moveStop(row: KitSelectRow): void {
    this.gotoRow(row);
    // Aria re-sorts moved rows only when its MutationObserver reports, which can be after this render,
    // so an index taken from the DOM may have named another row; by the microtask it has re-sorted.
    if (!row.active()) queueMicrotask(() => this.gotoRow(row));
  }

  /** `gotoIndex` counts rows in document order, the order Aria keeps them in. */
  private gotoRow(row: KitSelectRow): void {
    const rows = this.rows();
    if (!rows.includes(row)) return;
    const precedes = (other: KitSelectRow) =>
      (row.element.compareDocumentPosition(other.element) & Node.DOCUMENT_POSITION_PRECEDING) !== 0;
    this.listbox.gotoIndex(rows.filter(precedes).length);
  }
}
