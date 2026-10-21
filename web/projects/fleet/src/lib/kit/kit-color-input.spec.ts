import {
  Component,
  provideZonelessChangeDetection,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitColorInput } from './kit-color-input';

@Component({
  selector: 'fleet-test-host',
  imports: [KitColorInput],
  template: `<fleet-kit-color-input
    [value]="value()"
    [disabled]="disabled()"
    (valueChange)="committed.push($event)"
  />`,
})
class TestHost {
  readonly value = signal('#14b8a6');
  readonly disabled = signal(false);
  readonly committed: string[] = [];
}

function render() {
  TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection()],
  });
  const fixture = TestBed.createComponent(TestHost);
  fixture.detectChanges();
  const root: HTMLElement = fixture.nativeElement;
  return {
    fixture,
    host: fixture.componentInstance,
    text: root.querySelector<HTMLInputElement>('.text')!,
    picker: root.querySelector<HTMLInputElement>('.picker')!,
  };
}

function type(field: HTMLInputElement, value: string): void {
  field.value = value;
  field.dispatchEvent(new Event(field.type === 'color' ? 'change' : 'input'));
}

describe('KitColorInput', () => {
  it('shows the committed color in both controls', () => {
    const { text, picker } = render();
    expect(text.value).toBe('#14b8a6');
    expect(picker.value).toBe('#14b8a6');
  });

  it('commits one lowercase #rrggbb when a well-formed hex is typed', () => {
    const { fixture, host, text, picker } = render();
    type(text, '#FF8800');
    fixture.detectChanges();
    expect(host.committed).toEqual(['#ff8800']);
    expect(picker.value).toBe('#ff8800');
  });

  it('commits what the native picker chooses, and shows it in the text field', () => {
    const { fixture, host, text, picker } = render();
    type(picker, '#123456');
    fixture.detectChanges();
    expect(host.committed).toEqual(['#123456']);
    expect(text.value).toBe('#123456');
  });

  it('commits nothing for a malformed field and marks it invalid until it is fixed', () => {
    const { fixture, host, text, picker } = render();
    for (const bad of ['#abc', '#12345g', '14b8a6', '#14b8a6ff']) {
      type(text, bad);
    }
    fixture.detectChanges();
    expect(host.committed).toEqual([]);
    expect(text.getAttribute('aria-invalid')).toBe('true');
    expect(picker.value).toBe('#000000');

    type(text, '#abcdef');
    fixture.detectChanges();
    expect(host.committed).toEqual(['#abcdef']);
    expect(text.getAttribute('aria-invalid')).toBeNull();
  });

  it('follows a new committed value from the caller', () => {
    const { fixture, host, text, picker } = render();
    host.value.set('#ff8800');
    fixture.detectChanges();
    expect(text.value).toBe('#ff8800');
    expect(picker.value).toBe('#ff8800');
  });

  it('seeds an empty field, valid, from a value that is not a hex', () => {
    const { fixture, host, text } = render();
    host.value.set('teal');
    fixture.detectChanges();
    expect(text.value).toBe('');
    expect(text.getAttribute('aria-invalid')).toBeNull();
  });

  it('disables both controls while disabled', () => {
    const { fixture, host, text, picker } = render();
    expect(text.disabled).toBe(false);
    host.disabled.set(true);
    fixture.detectChanges();
    expect(text.disabled).toBe(true);
    expect(picker.disabled).toBe(true);
  });
});
