import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitPaceBar } from './kit-pace-bar';

@Component({
  selector: 'fleet-test-host',
  imports: [KitPaceBar],
  template: `<fleet-kit-pace-bar [label]="label()" [utilizationPct]="utilizationPct()" [elapsedPct]="elapsedPct()" />`,
})
class TestHost {
  readonly label = signal('5h');
  readonly utilizationPct = signal(40);
  readonly elapsedPct = signal(20);
}

async function render(label: string, utilizationPct: number, elapsedPct: number): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(TestHost);
  fixture.componentInstance.label.set(label);
  fixture.componentInstance.utilizationPct.set(utilizationPct);
  fixture.componentInstance.elapsedPct.set(elapsedPct);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('KitPaceBar', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the label and both bars at the given percentages', async () => {
    const el = await render('5h', 40, 20);

    expect(el.querySelector('[data-testid="pace-bar-label"]')?.textContent?.trim()).toBe('5h');
    const util = el.querySelector<HTMLElement>('[data-testid="pace-bar-utilization"]');
    const elapsed = el.querySelector<HTMLElement>('[data-testid="pace-bar-elapsed"]');
    expect(util?.getAttribute('aria-valuenow')).toBe('40');
    expect(elapsed?.getAttribute('aria-valuenow')).toBe('20');
    expect(util?.querySelector<HTMLElement>('.fill')?.style.width).toBe('40%');
    expect(elapsed?.querySelector<HTMLElement>('.fill')?.style.width).toBe('20%');
  });

  it('clamps a negative percentage to 0 rather than rendering it raw', async () => {
    const el = await render('7d', -15, -1);

    const util = el.querySelector<HTMLElement>('[data-testid="pace-bar-utilization"]');
    const elapsed = el.querySelector<HTMLElement>('[data-testid="pace-bar-elapsed"]');
    expect(util?.getAttribute('aria-valuenow')).toBe('0');
    expect(elapsed?.getAttribute('aria-valuenow')).toBe('0');
    expect(util?.querySelector<HTMLElement>('.fill')?.style.width).toBe('0%');
    expect(elapsed?.querySelector<HTMLElement>('.fill')?.style.width).toBe('0%');
  });

  it('clamps an over-100 percentage to 100 rather than overflowing', async () => {
    const el = await render('7d', 140, 250);

    const util = el.querySelector<HTMLElement>('[data-testid="pace-bar-utilization"]');
    const elapsed = el.querySelector<HTMLElement>('[data-testid="pace-bar-elapsed"]');
    expect(util?.getAttribute('aria-valuenow')).toBe('100');
    expect(elapsed?.getAttribute('aria-valuenow')).toBe('100');
    expect(util?.querySelector<HTMLElement>('.fill')?.style.width).toBe('100%');
    expect(elapsed?.querySelector<HTMLElement>('.fill')?.style.width).toBe('100%');
  });
});
