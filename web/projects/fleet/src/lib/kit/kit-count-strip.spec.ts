import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitCountStrip, type KitCountCell } from './kit-count-strip';

const CELLS: readonly KitCountCell[] = [
  { key: 'a', count: 1, caption: 'alpha', testid: 'cell-a', selectable: true, countColor: 'red' },
  { key: 'b', count: 2, caption: 'beta', testid: 'cell-b', selectable: true },
  { key: 'c', count: '3/4', caption: 'gamma', testid: 'cell-c', selectable: false },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitCountStrip],
  template: `<fleet-kit-count-strip testid="strip" [cells]="cells()" [selected]="selected()" (cellClick)="clicked.push($event)" />`,
})
class TestHost {
  readonly cells = signal<readonly KitCountCell[]>(CELLS);
  readonly selected = signal<string | null>(null);
  readonly clicked: string[] = [];
}

describe('KitCountStrip', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a button only for each selectable cell, a plain div for a static one', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const a = el.querySelector<HTMLButtonElement>('[data-testid="cell-a"]');
    expect(a?.tagName).toBe('BUTTON');
    expect(a?.type).toBe('button');
    expect(el.querySelector('[data-testid="cell-b"]')?.tagName).toBe('BUTTON');
    const c = el.querySelector<HTMLElement>('[data-testid="cell-c"]');
    expect(c?.tagName).toBe('DIV');
    expect(c?.hasAttribute('aria-pressed')).toBe(false);
    expect(c?.hasAttribute('tabindex')).toBe(false);
    expect(c?.textContent).toContain('3/4');
    expect(c?.textContent).toContain('gamma');
  });

  it('follows selected with aria-pressed and the on state', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const a = () => el.querySelector('[data-testid="cell-a"]');
    const b = () => el.querySelector('[data-testid="cell-b"]');

    expect(a()?.getAttribute('aria-pressed')).toBe('false');
    expect(b()?.getAttribute('aria-pressed')).toBe('false');

    fixture.componentInstance.selected.set('b');
    await fixture.whenStable();
    expect(a()?.getAttribute('aria-pressed')).toBe('false');
    expect(b()?.getAttribute('aria-pressed')).toBe('true');
    expect(b()?.classList.contains('on')).toBe(true);
  });

  it('emits the clicked cell key, and nothing for a static cell', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="cell-b"]')?.click();
    el.querySelector<HTMLElement>('[data-testid="cell-c"]')?.click();
    el.querySelector<HTMLElement>('[data-testid="cell-a"]')?.click();
    expect(fixture.componentInstance.clicked).toEqual(['b', 'a']);
  });

  it('colours a count from countColor', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLElement>('[data-testid="cell-a"] .cell-count')?.style.color).toBe('red');
    expect(el.querySelector<HTMLElement>('[data-testid="cell-b"] .cell-count')?.style.color).toBe('');
  });

  it('lays out one equal column per cell', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const strip = () => el.querySelector<HTMLElement>('[data-testid="strip"]');
    expect(strip()?.style.gridTemplateColumns).toBe('repeat(3, 1fr)');

    fixture.componentInstance.cells.set(CELLS.slice(0, 2));
    await fixture.whenStable();
    expect(strip()?.style.gridTemplateColumns).toBe('repeat(2, 1fr)');
  });
});
