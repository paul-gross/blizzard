import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { FormField, disabled, form } from '@angular/forms/signals';

import { KitCheckbox } from './kit-checkbox';
import { KitColorInput } from './kit-color-input';
import { KitOption } from './kit-option';
import { KitOptionGroup } from './kit-option-group';
import { KitSelect } from './kit-select';
import { KitTextInput } from './kit-text-input';

@Component({
  imports: [FormField, KitTextInput, KitCheckbox, KitSelect, KitColorInput, KitOptionGroup, KitOption],
  template: `
    <fleet-kit-text-input [formField]="f.text" />
    <fleet-kit-text-input [formField]="f.note" multiline />
    <fleet-kit-checkbox [formField]="f.flag" />
    <fleet-kit-select [formField]="f.pick" [options]="options" ariaLabel="Pick" />
    <fleet-kit-color-input [formField]="f.color" />
    <fleet-kit-option-group [formField]="f.mode" ariaLabel="Mode">
      <fleet-kit-option value="a">A</fleet-kit-option>
      <fleet-kit-option value="b">B</fleet-kit-option>
      <fleet-kit-option value="c" [disabled]="true">C</fleet-kit-option>
    </fleet-kit-option-group>
  `,
})
class Host {
  readonly locked = signal(false);
  readonly model = signal({ text: 'hi', note: 'n', flag: false, pick: 'x', color: '#112233', mode: 'a' });
  readonly f = form(this.model, (p) => {
    disabled(p.text, () => this.locked());
    disabled(p.note, () => this.locked());
    disabled(p.flag, () => this.locked());
    disabled(p.pick, () => this.locked());
    disabled(p.color, () => this.locked());
    disabled(p.mode, () => this.locked());
  });
  readonly options = [
    { value: 'x', label: 'Ex' },
    { value: 'y', label: 'Why' },
  ];
}

async function mount() {
  await TestBed.configureTestingModule({
    imports: [Host],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(Host);
  document.body.appendChild(fixture.nativeElement);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  return { fixture, host: fixture.componentInstance, root };
}

describe('kit controls bound through [formField]', () => {
  afterEach(() => (document.body.innerHTML = ''));

  it('text input: value both ways, blur touches, disabled and focus reach the native element', async () => {
    const { fixture, host, root } = await mount();
    const input = root.querySelector<HTMLInputElement>('fleet-kit-text-input input')!;
    expect(input.value).toBe('hi');
    input.value = 'typed';
    input.dispatchEvent(new Event('input'));
    expect(host.model().text).toBe('typed');
    host.f.text().value.set('set');
    await fixture.whenStable();
    expect(input.value).toBe('set');
    expect(host.f.text().touched()).toBe(false);
    input.dispatchEvent(new Event('blur'));
    expect(host.f.text().touched()).toBe(true);
    host.locked.set(true);
    await fixture.whenStable();
    expect(input.disabled).toBe(true);
  });

  it('multiline text input focuses its textarea', async () => {
    const { fixture, root } = await mount();
    const area = root.querySelector<HTMLTextAreaElement>('textarea')!;
    expect(area.value).toBe('n');
    fixture.debugElement.queryAll(By.directive(KitTextInput))[1].componentInstance.focus();
    expect(document.activeElement).toBe(area);
  });

  it('checkbox binds as a boolean field', async () => {
    const { fixture, host, root } = await mount();
    const box = root.querySelector<HTMLInputElement>('fleet-kit-checkbox input')!;
    box.click();
    expect(host.model().flag).toBe(true);
    await fixture.whenStable();
    host.f.flag().value.set(false);
    await fixture.whenStable();
    expect(box.checked).toBe(false);
    box.dispatchEvent(new Event('blur'));
    expect(host.f.flag().touched()).toBe(true);
    host.locked.set(true);
    await fixture.whenStable();
    expect(box.disabled).toBe(true);
  });

  it('select shows and sets the bound value, and touches when focus leaves', async () => {
    const { fixture, host, root } = await mount();
    const trigger = root.querySelector<HTMLButtonElement>('fleet-kit-select button')!;
    expect(trigger.textContent).toContain('Ex');
    trigger.click();
    await fixture.whenStable();
    document.body.querySelector<HTMLElement>('[role="option"]:nth-child(2)')!.click();
    await fixture.whenStable();
    expect(host.model().pick).toBe('y');
    expect(trigger.textContent).toContain('Why');
    trigger.dispatchEvent(new FocusEvent('focusout', { relatedTarget: document.body }));
    expect(host.f.pick().touched()).toBe(true);
    host.locked.set(true);
    await fixture.whenStable();
    expect(trigger.disabled).toBe(true);
  });

  it('color input commits only well-formed hex to the field', async () => {
    const { fixture, host, root } = await mount();
    const text = root.querySelector<HTMLInputElement>('fleet-kit-color-input input.text')!;
    text.value = '#12';
    text.dispatchEvent(new Event('input'));
    expect(host.model().color).toBe('#112233');
    text.value = '#ABCDEF';
    text.dispatchEvent(new Event('input'));
    expect(host.model().color).toBe('#abcdef');
    text.dispatchEvent(new FocusEvent('blur', { relatedTarget: document.body }));
    expect(host.f.color().touched()).toBe(true);
    host.locked.set(true);
    await fixture.whenStable();
    expect(text.disabled).toBe(true);
  });

  it('option group shares one native name, checks by value, and disables its radios', async () => {
    const { fixture, host, root } = await mount();
    const radios = [...root.querySelectorAll<HTMLInputElement>('fleet-kit-option-group input[type="radio"]')];
    expect(new Set(radios.map((r) => r.name)).size).toBe(1);
    expect(radios[0].name).not.toBe('');
    expect(radios.map((r) => r.checked)).toEqual([true, false, false]);
    expect(radios[2].disabled).toBe(true);
    radios[1].click();
    expect(host.model().mode).toBe('b');
    await fixture.whenStable();
    host.f.mode().value.set('a');
    await fixture.whenStable();
    expect(radios[0].checked).toBe(true);
    radios[0].dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: document.body }));
    expect(host.f.mode().touched()).toBe(true);
    host.locked.set(true);
    await fixture.whenStable();
    expect(radios.every((r) => r.disabled)).toBe(true);
  });
});
