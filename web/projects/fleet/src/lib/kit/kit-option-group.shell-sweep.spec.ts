import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { FormField, disabled, form } from '@angular/forms/signals';
import { userEvent } from 'vitest/browser';

import { KitOption } from './kit-option';
import { KitOptionGroup } from './kit-option-group';

@Component({
  imports: [FormField, KitOptionGroup, KitOption],
  template: `
    <fleet-kit-option-group [formField]="f.mode" ariaLabel="Mode">
      <fleet-kit-option value="a">A</fleet-kit-option>
      <fleet-kit-option value="b">B</fleet-kit-option>
      <fleet-kit-option value="c">C</fleet-kit-option>
    </fleet-kit-option-group>
  `,
})
class GroupHost {
  readonly locked = signal(false);
  readonly model = signal({ mode: 'a' });
  readonly f = form(this.model, (p) => disabled(p.mode, () => this.locked()));
}

describe('KitOptionGroup shell sweep', () => {
  it('is one native radio group whose arrow keys move the check and the form value', async () => {
    await TestBed.configureTestingModule({
      imports: [GroupHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(GroupHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      const radios = [...root.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
      expect(new Set(radios.map((r) => r.name)).size).toBe(1);

      await userEvent.tab();
      expect(document.activeElement).toBe(radios[0]);
      await userEvent.keyboard('{ArrowDown}');
      await fixture.whenStable();
      expect(radios[1].checked).toBe(true);
      expect(fixture.componentInstance.model().mode).toBe('b');
      await userEvent.keyboard('{ArrowUp}');
      await fixture.whenStable();
      expect(radios[0].checked).toBe(true);
      expect(fixture.componentInstance.model().mode).toBe('a');

      fixture.componentInstance.locked.set(true);
      await fixture.whenStable();
      expect(radios.every((r) => r.disabled)).toBe(true);
      radios[0].focus();
      expect(document.activeElement).not.toBe(radios[0]);
    } finally {
      root.remove();
    }
  });
});
