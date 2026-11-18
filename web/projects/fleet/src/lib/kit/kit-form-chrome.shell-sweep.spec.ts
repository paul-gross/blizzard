import {
  Component,
  provideZonelessChangeDetection,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';
import {
  FormField,
  disabled,
  form,
  required,
  schema,
  validate,
} from '@angular/forms/signals';
import { page, userEvent } from 'vitest/browser';

import { KitField } from './kit-field';
import { KitForm } from './kit-form';
import { submitDraft } from './kit-form-chrome.story-host';
import { KitNumberInput } from './kit-number-input';
import { KitOrDefault } from './kit-or-default';
import { KitTextInput } from './kit-text-input';

interface Draft {
  slug: string;
  useDefault: boolean;
  hours: string;
}

const rules = schema<Draft>((p) => {
  required(p.slug);
  disabled(p.hours, ({ valueOf }) => valueOf(p.useDefault));
  validate(p.hours, ({ value, valueOf }) =>
    valueOf(p.useDefault) || /^\d+$/.test(value())
      ? null
      : { kind: 'hours', message: 'Enter whole hours' },
  );
});

const LONG = 'The hub refused this save because '.concat(
  'a_very_long_unbroken_reason_'.repeat(8),
);

@Component({
  imports: [
    FormField,
    KitForm,
    KitField,
    KitTextInput,
    KitNumberInput,
    KitOrDefault,
  ],
  template: `
    <fleet-kit-form [form]="f" (save)="save()">
      <fleet-kit-field label="Slug" [field]="f.slug">
        <fleet-kit-text-input [formField]="f.slug" />
      </fleet-kit-field>
      <fleet-kit-field label="Park timeout" [field]="f.hours">
        <fleet-kit-or-default [formField]="f.useDefault" defaultLabel="Default">
          <fleet-kit-number-input [formField]="f.hours" unit="hours" />
        </fleet-kit-or-default>
      </fleet-kit-field>
    </fleet-kit-form>
  `,
})
class Host {
  sent = 0;
  readonly model = signal<Draft>({ slug: '', useDefault: false, hours: '' });
  readonly f = form(this.model, rules);
  save(): void {
    void submitDraft(this.f, async () => {
      this.sent += 1;
      return { ok: false, slug: LONG, form: LONG };
    });
  }
}

describe('form chrome shell sweep', () => {
  it('moves focus in source order, focuses the first error on Save, skips a defaulted number, and wraps at narrow widths', async () => {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await page.viewport(390, 800);
    await fixture.whenStable();

    try {
      const slug = root.querySelector<HTMLInputElement>(
        'fleet-kit-text-input input',
      )!;
      const num = root.querySelector<HTMLInputElement>(
        'fleet-kit-number-input input',
      )!;
      const chip = root.querySelector<HTMLButtonElement>(
        'fleet-kit-chip button',
      )!;
      const save = root.querySelector<HTMLButtonElement>(
        '[data-testid="kit-form-save"]',
      )!;
      const cancel = root.querySelector<HTMLButtonElement>(
        '[data-testid="kit-form-cancel"]',
      )!;

      // Tab order is source order.
      const order: Element[] = [];
      await userEvent.tab();
      for (let i = 0; i < 5; i++) {
        order.push(document.activeElement!);
        await userEvent.tab();
      }
      expect(order).toEqual([slug, num, chip, save, cancel]);

      // Save on an invalid form sends nothing and focuses the first field in error.
      save.focus();
      await userEvent.keyboard('{Enter}');
      await fixture.whenStable();
      expect(fixture.componentInstance.sent).toBe(0);
      expect(document.activeElement).toBe(slug);

      // Narrow widths: errors wrap, nothing overflows horizontally, the action row stays visible.
      fixture.componentInstance.f.slug().value.set('ok');
      fixture.componentInstance.f.hours().value.set('4');
      await fixture.whenStable();
      await userEvent.click(save);
      await fixture.whenStable();
      expect(fixture.componentInstance.sent).toBe(1);
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await fixture.whenStable();
        const formEl = root.querySelector('form')!;
        expect(formEl.scrollWidth).toBeLessThanOrEqual(formEl.clientWidth);
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
          document.documentElement.clientWidth,
        );
        for (const button of [save, cancel]) {
          const rect = button.getBoundingClientRect();
          expect(rect.left).toBeGreaterThanOrEqual(0);
          expect(rect.right).toBeLessThanOrEqual(width);
        }
        expect(
          root.querySelector('[data-testid="kit-form-line"]'),
        ).not.toBeNull();
      }

      // Choosing the default makes Tab skip the disabled number input.
      chip.click();
      await fixture.whenStable();
      expect(num.disabled).toBe(true);
      slug.focus();
      await userEvent.tab();
      expect(document.activeElement).toBe(chip);
    } finally {
      await page.viewport(1280, 800);
      root.remove();
    }
  });
});
