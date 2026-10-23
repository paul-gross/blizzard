import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitPaceBar, type PaceBarVariant } from './kit-pace-bar';

@Component({
  selector: 'fleet-test-host',
  imports: [KitPaceBar],
  template: `<fleet-kit-pace-bar [label]="label()" [utilizationPct]="utilizationPct()" [elapsedPct]="elapsedPct()" [variant]="variant()" [subject]="subject()" />`,
})
class TestHost {
  readonly label = signal('5h');
  readonly utilizationPct = signal(40);
  readonly elapsedPct = signal(20);
  readonly variant = signal<PaceBarVariant>('bars');
  readonly subject = signal<string | null>(null);
}

async function render(label: string, utilizationPct: number, elapsedPct: number, variant: PaceBarVariant = 'bars', subject: string | null = null): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(TestHost);
  fixture.componentInstance.label.set(label);
  fixture.componentInstance.utilizationPct.set(utilizationPct);
  fixture.componentInstance.elapsedPct.set(elapsedPct);
  fixture.componentInstance.variant.set(variant);
  fixture.componentInstance.subject.set(subject);
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

  it('closes the row with the utilization as a whole, clamped percent', async () => {
    expect((await render('5h', 61.6, 20)).querySelector('[data-testid="pace-bar-value"]')?.textContent?.trim()).toBe('62%');
    expect((await render('7d', 140, 20)).querySelector('[data-testid="pace-bar-value"]')?.textContent?.trim()).toBe('100%');
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

  it('draws the bars variant by default, with no blocks', async () => {
    const el = await render('5h', 40, 20);

    expect(el.querySelector('[data-testid="pace-bar-blocks"]')).toBeNull();
    expect(el.querySelector('[data-testid="pace-bar-utilization"]')).not.toBeNull();
    expect(el.querySelector('fleet-kit-pace-bar')?.classList.contains('ahead')).toBe(false);
  });

  describe('blocks variant', () => {
    const cells = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('.cell')];

    it('fills a block per tenth of usage and notches the clock before its block', async () => {
      const el = await render('5h', 62, 70, 'blocks');

      expect(el.querySelector('[data-testid="pace-bar-utilization"]')).toBeNull();
      expect(el.querySelector('[data-testid="pace-bar-label"]')?.textContent?.trim()).toBe('5h');
      expect(cells(el)).toHaveLength(10);
      expect(cells(el).map((c) => c.classList.contains('on'))).toEqual([true, true, true, true, true, true, false, false, false, false]);
      expect(cells(el).findIndex((c) => c.classList.contains('clock'))).toBe(7);
      expect(el.querySelector('.clock-end')).toBeNull();
      expect(el.querySelector('fleet-kit-pace-bar')?.classList.contains('ahead')).toBe(false);
    });

    it('notches a spent window after its last block', async () => {
      const el = await render('5h', 100, 100, 'blocks');

      expect(cells(el).every((c) => c.classList.contains('on'))).toBe(true);
      expect(el.querySelector('.cell.clock')).toBeNull();
      expect(cells(el)[9].classList.contains('clock-end')).toBe(true);
    });

    it('reads usage past the clock as ahead', async () => {
      const el = await render('7d', 80, 40, 'blocks');

      expect(el.querySelector('fleet-kit-pace-bar')?.classList.contains('ahead')).toBe(true);
    });

    it('clamps out-of-range figures', async () => {
      const over = await render('5h', 140, 250, 'blocks');
      expect(cells(over).every((c) => c.classList.contains('on'))).toBe(true);
      expect(cells(over)[9].classList.contains('clock-end')).toBe(true);
      expect(over.querySelector('[role="img"]')?.getAttribute('title')).toBe('5h · 100% used · 100% of window');

      const under = await render('5h', -5, -1, 'blocks');
      expect(cells(under).some((c) => c.classList.contains('on'))).toBe(false);
      expect(cells(under)[0].classList.contains('clock')).toBe(true);
    });

    it('names the window by its figures, led by the subject where one is given', async () => {
      const el = await render('5h', 62, 70, 'blocks', 'Claude Max');

      const gauge = el.querySelector('[role="img"]');
      expect(gauge?.getAttribute('title')).toBe('5h · 62% used · 70% of window');
      expect(gauge?.getAttribute('aria-label')).toBe('Claude Max 5h · 62% used · 70% of window');
    });
  });
});
