import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitCardOpen } from './kit-card-open';

@Component({
  selector: 'fleet-test-host',
  imports: [KitCardOpen],
  template: `
    <fleet-kit-card-open testid="open" ariaLabel="Open chunk ch_1" [current]="current()" (opened)="opens += 1">Card</fleet-kit-card-open>
  `,
})
class TestHost {
  readonly current = signal(false);
  opens = 0;
}

describe('KitCardOpen', () => {
  it('is a button that names the card, flags the current one and emits opened on click', async () => {
    await TestBed.configureTestingModule({ imports: [TestHost], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const button = (fixture.nativeElement as HTMLElement).querySelector('button.row') as HTMLButtonElement;

    expect(button.type).toBe('button');
    expect(button.getAttribute('data-testid')).toBe('open');
    expect(button.getAttribute('aria-label')).toBe('Open chunk ch_1');
    expect(button.hasAttribute('aria-current')).toBe(false);

    fixture.componentInstance.current.set(true);
    await fixture.whenStable();
    expect(button.getAttribute('aria-current')).toBe('true');

    button.click();
    expect(fixture.componentInstance.opens).toBe(1);
  });
});
