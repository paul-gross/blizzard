import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSelectList } from './kit-select-list';
import { KitSelectRow } from './kit-select-row';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSelectList, KitSelectRow],
  template: `
    <div fleetKitSelectList [value]="selected()" (valueChange)="opens.push($event)">
      <fleet-kit-select-row value="a" label="Row A" testid="row-a">Row A</fleet-kit-select-row>
      <fleet-kit-select-row value="b" label="Row B" bare ariaLabel="Open chunk ch_1">Row B</fleet-kit-select-row>
    </div>
  `,
})
class TestHost {
  readonly selected = signal<string | null>(null);
  readonly opens: string[] = [];
}

describe('KitSelectRow', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('is an option carrying the projected content and testid on its host', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="row-a"]') as HTMLElement;

    expect(row.getAttribute('role')).toBe('option');
    expect(row.textContent).toContain('Row A');
    expect(row.querySelector('button')).toBeNull();
  });

  it('reflects the list value onto the row and emits on click', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="row-a"]') as HTMLElement;

    expect(row.classList.contains('selected')).toBe(false);
    fixture.componentInstance.selected.set('a');
    await fixture.whenStable();
    expect(row.classList.contains('selected')).toBe(true);
    expect(row.getAttribute('aria-selected')).toBe('true');

    row.click();
    await fixture.whenStable();
    expect(fixture.componentInstance.opens).toEqual(['a']);
  });

  it('toggles the bare class and forwards ariaLabel onto the host', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('fleet-kit-select-row');

    expect(rows[0].classList.contains('bare')).toBe(false);
    expect(rows[0].hasAttribute('aria-label')).toBe(false);
    expect(rows[1].classList.contains('bare')).toBe(true);
    expect(rows[1].getAttribute('aria-label')).toBe('Open chunk ch_1');
  });
});
