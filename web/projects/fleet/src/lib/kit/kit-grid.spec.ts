import { Component, provideZonelessChangeDetection, viewChild } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitButton } from './kit-button';
import { KitGridFixture } from './kit-grid.fixture';
import { KitSelect } from './kit-select';

@Component({
  imports: [KitButton, KitSelect],
  template: `
    <fleet-kit-button testid="solo-button">Go</fleet-kit-button>
    <fleet-kit-select
      ariaLabel="Role"
      testid="solo-select"
      [options]="options"
      selectedValue="a"
      (opened)="events.push('opened')"
      (closed)="events.push('closed')"
    />
    <fleet-kit-button testid="forced" [controlTabIndex]="-1">Go</fleet-kit-button>
  `,
})
class Standalone {
  readonly options = [
    { value: 'a', label: 'Alpha' },
    { value: 'b', label: 'Bravo' },
  ];
  readonly events: string[] = [];
  readonly button = viewChild.required(KitButton);
  readonly select = viewChild.required(KitSelect);
}

@Component({ imports: [KitGridFixture], template: '<fleet-kit-grid-fixture />' })
class InGrid {}

describe('Kit grid bridge — jsdom contract', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Standalone, InGrid],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  const byId = (el: HTMLElement, id: string) => el.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;

  describe('outside a grid', () => {
    it('leaves the native tab order alone: no tabindex attribute unless controlTabIndex is bound', async () => {
      const fixture = TestBed.createComponent(Standalone);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(byId(el, 'solo-button').hasAttribute('tabindex')).toBe(false);
      expect(byId(el, 'solo-select').hasAttribute('tabindex')).toBe(false);
      expect(byId(el, 'forced').getAttribute('tabindex')).toBe('-1');
    });

    it('exposes the native button as controlElement()', async () => {
      const fixture = TestBed.createComponent(Standalone);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(fixture.componentInstance.button().controlElement()).toBe(byId(el, 'solo-button'));
      expect(fixture.componentInstance.select().controlElement()).toBe(byId(el, 'solo-select'));
    });

    it('openFromGrid and closeFromGrid are idempotent, and opened/closed emit once per change', async () => {
      const fixture = TestBed.createComponent(Standalone);
      await fixture.whenStable();
      const { select, events } = fixture.componentInstance;
      const trigger = byId(fixture.nativeElement, 'solo-select');

      select().closeFromGrid();
      expect(events).toEqual([]);

      select().openFromGrid();
      select().openFromGrid();
      await fixture.whenStable();
      expect(events).toEqual(['opened']);
      expect(trigger.getAttribute('aria-expanded')).toBe('true');

      select().closeFromGrid();
      select().closeFromGrid();
      await fixture.whenStable();
      expect(events).toEqual(['opened', 'closed']);
      expect(trigger.getAttribute('aria-expanded')).toBe('false');
    });

    it('emits opened and closed for a pointer open and close too', async () => {
      const fixture = TestBed.createComponent(Standalone);
      await fixture.whenStable();
      const trigger = byId(fixture.nativeElement, 'solo-select');
      trigger.click();
      await fixture.whenStable();
      trigger.click();
      await fixture.whenStable();
      expect(fixture.componentInstance.events).toEqual(['opened', 'closed']);
    });
  });

  describe('inside a grid', () => {
    it('gives the active cell the only Tab stop: a control is 0 only when its cell is active, and a disabled control never is', async () => {
      const fixture = TestBed.createComponent(InGrid);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      // No cell is active until focus enters the grid, so every control is out of the Tab order.
      for (const id of ['grid-select-ada', 'grid-button-ada', 'grid-select-grace', 'grid-button-grace'])
        expect(byId(el, id).getAttribute('tabindex')).toBe('-1');
    });

    it('marks cells and widgets with the literal host attributes the grid routes events by', async () => {
      const fixture = TestBed.createComponent(InGrid);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelectorAll('td[ngGridCell]').length).toBe(6);
      expect(el.querySelectorAll('fleet-kit-select[ngGridCellWidget], fleet-kit-button[ngGridCellWidget]').length).toBe(4);
      expect(el.querySelector('table')?.getAttribute('aria-label')).toBe('Members');
      expect(el.querySelector('table')?.getAttribute('role')).toBe('grid');
    });
  });
});
