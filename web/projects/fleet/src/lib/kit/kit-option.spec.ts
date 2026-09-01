import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitOption } from './kit-option';

@Component({
  selector: 'fleet-test-host',
  imports: [KitOption],
  template: `
    <fleet-kit-option
      name="choice"
      [checked]="checked()"
      [disabled]="disabled()"
      [alignTop]="alignTop()"
      testid="opt-a"
      (changed)="changes += 1"
    >
      Option A
    </fleet-kit-option>
  `,
})
class TestHost {
  readonly checked = signal(false);
  readonly disabled = signal(false);
  readonly alignTop = signal(false);
  changes = 0;
}

describe('KitOption', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a radio input associated with its own label, carrying the projected content', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const label = el.querySelector('label.opt') as HTMLLabelElement;
    const input = label.querySelector('input[type="radio"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    expect(input.name).toBe('choice');
    expect(input.getAttribute('data-testid')).toBe('opt-a');
    expect(label.textContent).toContain('Option A');
  });

  it('reflects checked and disabled onto the input, and emits changed on click', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.checked.set(true);
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector('input[type="radio"]') as HTMLInputElement;
    expect(input.checked).toBe(true);
    expect(input.disabled).toBe(true);

    fixture.componentInstance.disabled.set(false);
    await fixture.whenStable();
    input.dispatchEvent(new Event('change'));
    expect(fixture.componentInstance.changes).toBe(1);
  });

  it('aligns to flex-start when alignTop is set, center otherwise', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const label = el.querySelector('label.opt') as HTMLElement;

    expect(label.classList.contains('opt--top')).toBe(false);

    fixture.componentInstance.alignTop.set(true);
    await fixture.whenStable();
    expect(label.classList.contains('opt--top')).toBe(true);
  });
});
