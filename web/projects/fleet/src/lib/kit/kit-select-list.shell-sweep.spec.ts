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
  it('is one Tab stop; arrows, Home, End and type-ahead move without opening; Enter and click open', async () => {
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
    const before = root.querySelector<HTMLElement>('[data-testid="before"]')!;
    const after = root.querySelector<HTMLElement>('[data-testid="after"]')!;
    const scroller = root.querySelector<HTMLElement>(
      '[data-testid="scroller"]',
    )!;
    try {
      // One Tab stop: from the element before, Tab lands on the selected row; the next Tab leaves.
      before.focus();
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(row('row-03'));
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(after);

      // Arrows, Home, End and type-ahead move focus and never open.
      row('row-03').focus();
      await userEvent.keyboard('{ArrowDown}');
      expect(document.activeElement).toBe(row('row-04'));
      await userEvent.keyboard('{ArrowUp}{ArrowUp}');
      expect(document.activeElement).toBe(row('row-02'));
      await userEvent.keyboard('{End}');
      expect(document.activeElement).toBe(row('row-40'));
      const scrollerRect = scroller.getBoundingClientRect();
      const lastRect = row('row-40').getBoundingClientRect();
      expect(lastRect.bottom).toBeLessThanOrEqual(scrollerRect.bottom + 1);
      await userEvent.keyboard('{Home}');
      expect(document.activeElement).toBe(row('row-01'));
      await userEvent.keyboard('row-2');
      await expect.poll(() => document.activeElement).toBe(row('row-20'));
      expect(host.opened).toEqual([]);

      // Enter opens the active row.
      await userEvent.keyboard('{Enter}');
      expect(host.opened).toEqual(['row-20']);

      // An outside value change moves the Tab stop and keeps scroll and focus where they were.
      before.focus();
      scroller.scrollTop = 120;
      host.value.set('row-30');
      await fixture.whenStable();
      expect(scroller.scrollTop).toBe(120);
      expect(document.activeElement).toBe(before);
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(row('row-30'));

      // A pointer click opens and moves the active row.
      await userEvent.click(row('row-31'));
      expect(host.opened.at(-1)).toBe('row-31');
      expect(document.activeElement).toBe(row('row-31'));
    } finally {
      root.remove();
    }
  });
});
