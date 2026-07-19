import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSlotBar } from './kit-slot-bar';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSlotBar],
  template: `<fleet-kit-slot-bar [used]="used()" [total]="total()" />`,
})
class TestHost {
  readonly used = signal(2);
  readonly total = signal(4);
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

  it('clamps an over-count so it never renders more filled cells than exist', async () => {
    const el = await render(9, 4);

    const cells = el.querySelectorAll('.cell');
    expect(cells).toHaveLength(4);
    expect([...cells].every((c) => c.classList.contains('on'))).toBe(true);
  });
});
