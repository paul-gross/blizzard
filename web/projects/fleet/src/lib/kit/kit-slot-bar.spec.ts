import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSlotBar } from './kit-slot-bar';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSlotBar],
  template: `<fleet-kit-slot-bar [used]="used()" [total]="total()" [unit]="unit()" [name]="name()" />`,
})
class TestHost {
  readonly used = signal(2);
  readonly total = signal(4);
  readonly unit = signal('slots');
  readonly name = signal<string | null>(null);
}

async function render(used: number, total: number): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(TestHost);
  fixture.componentInstance.used.set(used);
  fixture.componentInstance.total.set(total);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('KitSlotBar', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders total cells with the first used filled and a used/total label', async () => {
    const el = await render(2, 4);

    const cells = el.querySelectorAll('.cell');
    expect(cells).toHaveLength(4);
    const filled = [...cells].map((c) => c.classList.contains('on'));
    expect(filled).toEqual([true, true, false, false]);
    expect(el.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/4 slots');
  });

  it('renders no filled cells when nothing is used', async () => {
    const el = await render(0, 4);

    const cells = el.querySelectorAll('.cell');
    expect(cells).toHaveLength(4);
    expect([...cells].some((c) => c.classList.contains('on'))).toBe(false);
    expect(el.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('0/4 slots');
  });

  it('fills every cell when the pool is fully used', async () => {
    const el = await render(4, 4);

    const cells = el.querySelectorAll('.cell');
    expect([...cells].every((c) => c.classList.contains('on'))).toBe(true);
    expect(el.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('4/4 slots');
  });

  it('draws an over-count past the total as that many more cells, each marked over', async () => {
    const el = await render(6, 4);
    const cells = [...el.querySelectorAll('.cell')];
    expect(cells).toHaveLength(6);
    expect(cells.every((c) => c.classList.contains('on'))).toBe(true);
    expect(cells.map((c) => c.classList.contains('over'))).toEqual([false, false, false, false, true, true]);
    expect(el.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('6/4 slots');
  });

  it('marks no cell over at or under the total, and fills none for a negative used', async () => {
    expect((await render(4, 4)).querySelectorAll('.cell.over')).toHaveLength(0);
    const negative = await render(-2, 4);
    expect(negative.querySelectorAll('.cell')).toHaveLength(4);
    expect(negative.querySelectorAll('.cell.on')).toHaveLength(0);
  });

  it('counts the unit it is given in the label, slots by default', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.unit.set('envs');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="slot-bar-label"]')?.textContent?.trim()).toBe('2/4 envs');
  });

  it('draws a named bar as one line — the name, then the cells, the figures only in its tooltip', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.name.set('blizzard');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="slot-bar-name"]')?.textContent?.trim()).toBe('blizzard');
    expect(el.querySelector('[data-testid="slot-bar-label"]')).toBeNull();
    expect(el.querySelectorAll('.cell.on')).toHaveLength(2);
    const gauge = el.querySelector('[data-testid="slot-bar-gauge"]');
    expect(gauge?.getAttribute('title')).toBe('2/4 slots');
    expect(gauge?.getAttribute('aria-label')).toBe('blizzard 2/4 slots');
  });

  it('draws no name on an unnamed bar', async () => {
    const el = await render(2, 4);

    expect(el.querySelector('[data-testid="slot-bar-name"]')).toBeNull();
  });
});
