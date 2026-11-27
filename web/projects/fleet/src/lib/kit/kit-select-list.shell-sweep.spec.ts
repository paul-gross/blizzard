import {
  Component,
  provideZonelessChangeDetection,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page, userEvent } from 'vitest/browser';

import { KitSelectList } from './kit-select-list';
import { KitSelectRow } from './kit-select-row';

const ROWS = Array.from(
  { length: 40 },
  (_, i) => `row-${String(i + 1).padStart(2, '0')}`,
);

@Component({
  imports: [KitSelectList, KitSelectRow],
  template: `
    <button data-testid="before" type="button">before</button>
    <div class="scroller" data-testid="scroller">
      <div
        fleetKitSelectList
        selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant"
        ariaLabel="Rows"
        [value]="value()"
        (valueChange)="opened.push($event)"
      >
        @for (row of rows; track row) {
          <fleet-kit-select-row [value]="row" [label]="row" [testid]="row">{{
            row
          }}</fleet-kit-select-row>
        }
      </div>
    </div>
    <button data-testid="after" type="button">after</button>
  `,
  styles: `
    .scroller {
      height: 200px;
      overflow: auto;
    }
  `,
})
class Host {
  readonly rows = ROWS;
  readonly value = signal<string | null>('row-03');
  readonly opened: string[] = [];
}

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile(
    'projects/fleet/src/lib/core/design/tokens.css',
  );
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

describe('KitSelectList keyboard model shell sweep', () => {
  it('is one Tab stop holding focus; arrows, Home, End and type-ahead move the active row without opening', async () => {
    await page.viewport(800, 600);
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    const host = fixture.componentInstance;
    const row = (id: string) =>
      root.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
    const list = root.querySelector<HTMLElement>('[role="listbox"]')!;
    const before = root.querySelector<HTMLElement>('[data-testid="before"]')!;
    const after = root.querySelector<HTMLElement>('[data-testid="after"]')!;
    const scroller = root.querySelector<HTMLElement>(
      '[data-testid="scroller"]',
    )!;
    const active = () => {
      const id = list.getAttribute('aria-activedescendant');
      return id === null ? null : document.getElementById(id);
    };
    const inView = (id: string) => {
      const view = scroller.getBoundingClientRect();
      const rect = row(id).getBoundingClientRect();
      return rect.top >= view.top - 1 && rect.bottom <= view.bottom + 1;
    };
    const outline = (el: HTMLElement) => getComputedStyle(el).outlineStyle;
    try {
      // One Tab stop: from the element before, Tab lands on the list, its selected row active; the next
      // Tab leaves.
      before.focus();
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(list);
      expect(active()).toBe(row('row-03'));
      expect(outline(list)).toBe('none');
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(after);

      // Arrows, Home, End and type-ahead move the active descendant and never open.
      await userEvent.keyboard('{Shift>}{Tab}{/Shift}');
      await userEvent.keyboard('{ArrowDown}');
      expect(active()).toBe(row('row-04'));
      await userEvent.keyboard('{ArrowUp}{ArrowUp}');
      expect(active()).toBe(row('row-02'));
      await userEvent.keyboard('{End}');
      expect(active()).toBe(row('row-40'));
      await expect.poll(() => inView('row-40')).toBe(true);
      await userEvent.keyboard('{Home}');
      expect(active()).toBe(row('row-01'));
      await expect.poll(() => inView('row-01')).toBe(true);
      await userEvent.keyboard('row-2');
      await expect.poll(active).toBe(row('row-20'));
      await expect.poll(() => inView('row-20')).toBe(true);
      expect(document.activeElement).toBe(list);
      expect(host.opened).toEqual([]);

      // Enter opens the active row.
      await userEvent.keyboard('{Enter}');
      expect(host.opened).toEqual(['row-20']);

      // Tabbing back onto a list whose active row is scrolled away shows it again. Leaving put the
      // active row back on the selected one.
      before.focus();
      scroller.scrollTop = scroller.scrollHeight;
      expect(inView('row-03')).toBe(false);
      await userEvent.keyboard('{Tab}');
      expect(active()).toBe(row('row-03'));
      await expect.poll(() => inView('row-03')).toBe(true);

      // An outside value change moves the active row and keeps scroll and focus where they were.
      before.focus();
      scroller.scrollTop = 120;
      host.value.set('row-30');
      await fixture.whenStable();
      expect(active()).toBe(row('row-30'));
      expect(scroller.scrollTop).toBe(120);
      expect(document.activeElement).toBe(before);
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(list);
      expect(active()).toBe(row('row-30'));

      // A pointer press opens the row, leaves focus on the list and draws no outline; Shift+Tab then
      // leaves the list in one press.
      before.focus();
      await userEvent.click(row('row-31'));
      expect(host.opened.at(-1)).toBe('row-31');
      expect(document.activeElement).toBe(list);
      expect(active()).toBe(row('row-31'));
      expect(list.matches(':focus-visible')).toBe(false);
      expect(outline(row('row-31').querySelector<HTMLElement>('.row')!)).toBe('none');
      expect(outline(list)).toBe('none');
      await userEvent.keyboard('{Shift>}{Tab}{/Shift}');
      expect(document.activeElement).toBe(before);
    } finally {
      root.remove();
    }
  });
});
