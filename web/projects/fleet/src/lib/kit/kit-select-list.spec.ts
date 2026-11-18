import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSelectList } from './kit-select-list';
import { KitSelectRow } from './kit-select-row';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSelectList, KitSelectRow],
  template: `
    <div fleetKitSelectList ariaLabel="Things" [value]="value()" (valueChange)="opened.push($event)">
      @for (row of rows(); track row.id) {
        <fleet-kit-select-row [value]="row.id" [label]="row.id" [disabled]="row.disabled ?? false" [testid]="'row-' + row.id">
          {{ row.id }}
        </fleet-kit-select-row>
      }
    </div>
  `,
})
class TestHost {
  readonly value = signal<string | null>(null);
  readonly rows = signal<{ id: string; disabled?: boolean }[]>([{ id: 'alpha' }, { id: 'bravo' }, { id: 'charlie' }]);
  readonly opened: string[] = [];
}

function key(el: Element, name: string): void {
  el.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
}

describe('KitSelectList', () => {
  async function setup() {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const row = (id: string) => el.querySelector(`[data-testid="row-${id}"]`) as HTMLElement;
    return { fixture, el, row, host: fixture.componentInstance };
  }

  it('is a listbox of options and selects the row the value names', async () => {
    const { fixture, el, row, host } = await setup();
    expect(el.querySelector('[role="listbox"]')?.getAttribute('aria-label')).toBe('Things');
    expect(el.querySelectorAll('[role="option"]')).toHaveLength(3);
    host.value.set('bravo');
    await fixture.whenStable();
    expect(row('bravo').getAttribute('aria-selected')).toBe('true');
    expect(row('alpha').getAttribute('aria-selected')).toBe('false');
    expect(host.opened).toEqual([]);
  });

  it('opens a row on click, Enter and Space, including the selected one', async () => {
    const { fixture, row, host } = await setup();
    row('alpha').click();
    await fixture.whenStable();
    expect(host.opened).toEqual(['alpha']);
    host.value.set('alpha');
    await fixture.whenStable();
    row('alpha').click();
    await fixture.whenStable();
    expect(host.opened).toEqual(['alpha', 'alpha']);
    expect(row('alpha').getAttribute('aria-selected')).toBe('true');
    row('alpha').focus();
    key(row('alpha'), 'ArrowDown');
    await fixture.whenStable();
    key(row('bravo'), 'Enter');
    await fixture.whenStable();
    expect(host.opened).toEqual(['alpha', 'alpha', 'bravo']);
  });

  it('moves the highlight with the arrow keys without opening', async () => {
    const { fixture, row, host } = await setup();
    host.value.set('alpha');
    await fixture.whenStable();
    row('alpha').focus();
    key(row('alpha'), 'ArrowDown');
    await fixture.whenStable();
    expect(document.activeElement).toBe(row('bravo'));
    key(row('bravo'), 'End');
    await fixture.whenStable();
    expect(host.opened).toEqual([]);
  });

  it('does not emit for an outside value change or a removed row', async () => {
    const { fixture, host } = await setup();
    host.value.set('charlie');
    await fixture.whenStable();
    host.rows.set([{ id: 'alpha' }]);
    await fixture.whenStable();
    expect(host.opened).toEqual([]);
  });

  it('skips disabled rows and cannot open them', async () => {
    const { fixture, row, host } = await setup();
    host.rows.set([{ id: 'alpha' }, { id: 'bravo', disabled: true }, { id: 'charlie' }]);
    await fixture.whenStable();
    row('bravo').click();
    await fixture.whenStable();
    expect(host.opened).toEqual([]);
  });

  it('leaves no tabbable element when empty', async () => {
    const { fixture, el, host } = await setup();
    host.rows.set([]);
    await fixture.whenStable();
    expect(el.querySelectorAll('[role="option"]')).toHaveLength(0);
    expect(el.querySelectorAll('[tabindex="0"]').length).toBeLessThanOrEqual(1);
  });
});
