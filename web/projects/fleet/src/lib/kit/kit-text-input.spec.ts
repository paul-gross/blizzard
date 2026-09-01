import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitTextInput } from './kit-text-input';

@Component({
  selector: 'fleet-test-host',
  imports: [KitTextInput],
  template: `
    <fleet-kit-text-input
      [value]="value()"
      [placeholder]="placeholder()"
      [ariaLabel]="ariaLabel()"
      [multiline]="multiline()"
      [rows]="rows()"
      testid="fld"
      (valueChange)="value.set($event)"
    />
  `,
})
class TestHost {
  readonly value = signal('');
  readonly placeholder = signal<string | null>(null);
  readonly ariaLabel = signal<string | null>(null);
  readonly multiline = signal(false);
  readonly rows = signal(3);
}

describe('KitTextInput', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a single-line input by default, carrying placeholder/aria-label/testid', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.placeholder.set('Type an answer…');
    fixture.componentInstance.ariaLabel.set('Answer question qn_01');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const control = el.querySelector<HTMLInputElement>('[data-testid="fld"]');
    expect(control?.tagName).toBe('INPUT');
    expect(control?.placeholder).toBe('Type an answer…');
    expect(control?.getAttribute('aria-label')).toBe('Answer question qn_01');
  });

  it('reflects the value input onto the control', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.value.set('rest');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLInputElement>('[data-testid="fld"]')?.value).toBe('rest');
  });

  it('emits valueChange with the typed value on input', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const control = el.querySelector<HTMLInputElement>('[data-testid="fld"]')!;
    control.value = 'graphql';
    control.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    expect(fixture.componentInstance.value()).toBe('graphql');
    expect(control.value).toBe('graphql');
  });

  it('renders a textarea with the given rows when multiline is set', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.multiline.set(true);
    fixture.componentInstance.rows.set(5);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const control = el.querySelector<HTMLTextAreaElement>('[data-testid="fld"]');
    expect(control?.tagName).toBe('TEXTAREA');
    expect(control?.rows).toBe(5);
  });

  it('emits valueChange from the textarea too, once multiline', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.multiline.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const control = el.querySelector<HTMLTextAreaElement>('[data-testid="fld"]')!;
    control.value = 'a charge note';
    control.dispatchEvent(new Event('input'));
    await fixture.whenStable();

    expect(fixture.componentInstance.value()).toBe('a charge note');
  });

  it('is a genuinely focusable native control, the element the :focus-visible chrome rule keys off of', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const control = el.querySelector<HTMLInputElement>('[data-testid="fld"]')!;
    control.focus();
    expect(document.activeElement).toBe(control);
  });
});
