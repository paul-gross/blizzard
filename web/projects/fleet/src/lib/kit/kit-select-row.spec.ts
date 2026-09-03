import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSelectRow } from './kit-select-row';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSelectRow],
  template: `
    <fleet-kit-select-row [selected]="selected()" testid="row-a" (picked)="picks += 1">
      Row A
    </fleet-kit-select-row>
  `,
})
class TestHost {
  readonly selected = signal(false);
  picks = 0;
}

describe('KitSelectRow', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a button carrying the projected content and testid', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const button = el.querySelector('button.row') as HTMLButtonElement;
    expect(button).toBeTruthy();
    expect(button.type).toBe('button');
    expect(button.getAttribute('data-testid')).toBe('row-a');
    expect(button.textContent).toContain('Row A');
  });

  it('reflects selected onto the row and emits picked on click', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const button = el.querySelector('button.row') as HTMLButtonElement;

    expect(button.classList.contains('selected')).toBe(false);

    fixture.componentInstance.selected.set(true);
    await fixture.whenStable();
    expect(button.classList.contains('selected')).toBe(true);

    button.click();
    expect(fixture.componentInstance.picks).toBe(1);
  });
});
