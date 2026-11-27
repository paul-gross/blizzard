import { NgTemplateOutlet } from '@angular/common';
import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed, type ComponentFixture } from '@angular/core/testing';

import { KitSelectList } from './kit-select-list';
import { KitSelectRow } from './kit-select-row';

const REQUIRED = ['selectionMode="explicit"', '[softDisabled]="false"', '[wrap]="false"', 'focusMode="activedescendant"'];

@Component({
  selector: 'fleet-test-host',
  imports: [KitSelectList, KitSelectRow],
  template: `
    <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" ariaLabel="Things" [value]="value()" (valueChange)="opened.push($event)">
      @for (row of rows(); track row.id) {
        <fleet-kit-select-row [value]="row.id" [label]="row.id" [disabled]="row.disabled ?? false" [testid]="'row-' + row.id">
          {{ row.id }}
          <a href="#x" [attr.data-testid]="'link-' + row.id">link</a>
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

/** Rows stamped through an outlet, so the order Angular creates them in is not the order they sit in. */
@Component({
  selector: 'fleet-outlet-host',
  imports: [KitSelectList, KitSelectRow, NgTemplateOutlet],
  template: `
    <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" [value]="value()">
      @for (id of top(); track id) {
        <ng-container *ngTemplateOutlet="row; context: { $implicit: id }" />
      }
      <div role="group">
        @for (id of bottom(); track id) {
          <ng-container *ngTemplateOutlet="row; context: { $implicit: id }" />
        }
      </div>
      <ng-template #row let-id>
        <fleet-kit-select-row [value]="id" [label]="id" [testid]="'row-' + id">{{ id }}</fleet-kit-select-row>
      </ng-template>
    </div>
  `,
})
class OutletHost {
  readonly value = signal<string | null>('bravo');
  readonly top = signal<string[]>([]);
  readonly bottom = signal<string[]>(['bravo', 'charlie']);
}

function key(el: Element, name: string): void {
  el.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
}

function read(fixture: ComponentFixture<unknown>) {
  const el = fixture.nativeElement as HTMLElement;
  const list = el.querySelector<HTMLElement>('[role="listbox"]')!;
  const row = (id: string) => el.querySelector<HTMLElement>(`[data-testid="row-${id}"]`)!;
  /** The testid of the row the list names as its active descendant. */
  const highlighted = () => {
    const id = list.getAttribute('aria-activedescendant');
    const active = Array.from(el.querySelectorAll<HTMLElement>('[role="option"]')).filter((o) => o.getAttribute('data-active') === 'true');
    expect(active.map((o) => o.id)).toEqual(id === null ? [] : [id]);
    return active[0]?.getAttribute('data-testid') ?? null;
  };
  return { el, list, row, highlighted };
}

/** Lets a render and the microtask a highlight move may queue both run. */
async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  await fixture.whenStable();
  await new Promise((resolve) => setTimeout(resolve));
  await fixture.whenStable();
}

describe('KitSelectList', () => {
  // jsdom lays nothing out, so it has no `scrollIntoView`.
  const scrolled: string[] = [];
  beforeAll(() => {
    HTMLElement.prototype.scrollIntoView = function (this: HTMLElement) {
      scrolled.push(this.getAttribute('data-testid') ?? '');
    };
  });
  afterAll(() => {
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
  });

  async function setup() {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);
    scrolled.length = 0;
    return { fixture, host: fixture.componentInstance, ...read(fixture) };
  }

  it('is a listbox of options and selects the row the value names', async () => {
    const { fixture, list, el, row, highlighted, host } = await setup();
    expect(list.getAttribute('aria-label')).toBe('Things');
    expect(list.getAttribute('tabindex')).toBe('0');
    expect(el.querySelectorAll('[role="option"]')).toHaveLength(3);
    host.value.set('bravo');
    await settle(fixture);
    expect(row('bravo').getAttribute('aria-selected')).toBe('true');
    expect(row('alpha').getAttribute('aria-selected')).toBe('false');
    expect(highlighted()).toBe('row-bravo');
    expect(host.opened).toEqual([]);
  });

  it('opens a row on click, Enter and Space, including the selected one', async () => {
    const { fixture, list, row, host } = await setup();
    row('alpha').click();
    await settle(fixture);
    expect(host.opened).toEqual(['alpha']);
    host.value.set('alpha');
    await settle(fixture);
    row('alpha').click();
    await settle(fixture);
    expect(host.opened).toEqual(['alpha', 'alpha']);
    expect(row('alpha').getAttribute('aria-selected')).toBe('true');
    list.focus();
    key(list, 'ArrowDown');
    await settle(fixture);
    key(list, 'Enter');
    await settle(fixture);
    key(list, ' ');
    await settle(fixture);
    expect(host.opened).toEqual(['alpha', 'alpha', 'bravo', 'bravo']);
  });

  it('re-opens the same row repeatedly when the consumer keeps no value', async () => {
    const { fixture, row, host } = await setup();
    row('alpha').click();
    await settle(fixture);
    row('alpha').click();
    await settle(fixture);
    expect(host.opened).toEqual(['alpha', 'alpha']);
  });

  it('moves the highlight with the arrow keys without opening or moving focus, scrolling it into view', async () => {
    const { fixture, list, highlighted, host } = await setup();
    host.value.set('alpha');
    await settle(fixture);
    expect(scrolled).toEqual([]);
    list.focus();
    scrolled.length = 0;
    key(list, 'ArrowDown');
    await settle(fixture);
    expect(highlighted()).toBe('row-bravo');
    expect(scrolled).toEqual(['row-bravo']);
    key(list, 'End');
    await settle(fixture);
    expect(highlighted()).toBe('row-charlie');
    key(list, 'Home');
    await settle(fixture);
    expect(highlighted()).toBe('row-alpha');
    expect(document.activeElement).toBe(list);
    expect(host.opened).toEqual([]);
  });

  it('does not emit or scroll for an outside value change or a removed row', async () => {
    const { fixture, highlighted, host } = await setup();
    host.value.set('charlie');
    await settle(fixture);
    expect(highlighted()).toBe('row-charlie');
    host.rows.set([{ id: 'alpha' }]);
    await settle(fixture);
    expect(highlighted()).toBe('row-alpha');
    expect(host.opened).toEqual([]);
    expect(scrolled).toEqual([]);
  });

  it('keeps the highlight under focus and moves it to the selected row when focus leaves', async () => {
    const { fixture, list, highlighted, host } = await setup();
    host.value.set('alpha');
    await settle(fixture);
    list.focus();
    key(list, 'ArrowDown');
    await settle(fixture);
    host.value.set('charlie');
    await settle(fixture);
    expect(highlighted()).toBe('row-bravo');
    list.blur();
    await settle(fixture);
    expect(highlighted()).toBe('row-charlie');
  });

  it('skips disabled rows and cannot open them', async () => {
    const { fixture, list, row, highlighted, host } = await setup();
    host.rows.set([{ id: 'alpha' }, { id: 'bravo', disabled: true }, { id: 'charlie' }]);
    await settle(fixture);
    row('bravo').click();
    await settle(fixture);
    expect(host.opened).toEqual([]);
    list.focus();
    key(list, 'Home');
    key(list, 'ArrowDown');
    await settle(fixture);
    expect(highlighted()).toBe('row-charlie');
  });

  it('owns no Tab stop when empty, and takes it back when rows return', async () => {
    const { fixture, el, list, host } = await setup();
    for (let round = 0; round < 2; round++) {
      host.rows.set([]);
      await settle(fixture);
      expect(el.querySelectorAll('[role="option"]')).toHaveLength(0);
      expect(el.querySelectorAll('[tabindex="0"]')).toHaveLength(0);
      host.rows.set([{ id: 'alpha' }]);
      await settle(fixture);
      expect(list.getAttribute('tabindex')).toBe('0');
    }
  });

  it('takes focus from a focused row, highlighting that row and scrolling nothing', async () => {
    const { fixture, list, row, highlighted, host } = await setup();
    host.value.set('alpha');
    await settle(fixture);
    row('charlie').focus();
    await settle(fixture);
    expect(document.activeElement).toBe(list);
    expect(highlighted()).toBe('row-charlie');
    expect(scrolled).toEqual([]);
    expect(host.opened).toEqual([]);
  });

  it('leaves focus on a focusable element inside a row', async () => {
    const { fixture, el } = await setup();
    const link = el.querySelector<HTMLElement>('[data-testid="link-bravo"]')!;
    link.focus();
    await settle(fixture);
    expect(document.activeElement).toBe(link);
  });

  async function interacted() {
    await TestBed.configureTestingModule({
      imports: [OutletHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(OutletHost);
    await settle(fixture);
    const dom = read(fixture);
    // Once a person has been on the list, Aria stops placing the highlight by itself.
    dom.list.focus();
    dom.list.blur();
    await settle(fixture);
    return { fixture, host: fixture.componentInstance, ...dom };
  }

  it('highlights a row stamped through an outlet and inserted ahead of earlier rows', async () => {
    const { fixture, highlighted, host } = await interacted();
    host.top.set(['alpha']);
    await settle(fixture);
    host.value.set('alpha');
    await settle(fixture);
    expect(highlighted()).toBe('row-alpha');
  });

  it('highlights the right row when rows reorder in the same render as the value changes', async () => {
    const { fixture, highlighted, host } = await interacted();
    host.bottom.set(['charlie', 'bravo']);
    host.value.set('charlie');
    await settle(fixture);
    expect(highlighted()).toBe('row-charlie');
  });

  it.each(REQUIRED)('fails a host that drops %s, naming it', (dropped) => {
    @Component({
      selector: 'fleet-test-unbound',
      imports: [KitSelectList],
      template: '<div fleetKitSelectList></div>',
    })
    class Unbound {}
    TestBed.overrideTemplate(Unbound, `<div fleetKitSelectList ${REQUIRED.filter((b) => b !== dropped).join(' ')}></div>`);

    expect(() => TestBed.createComponent(Unbound).detectChanges()).toThrow(`fleetKitSelectList is missing ${dropped}`);
  });
});
