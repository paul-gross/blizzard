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

import { KitField } from './kit-field';
import { KitForm } from './kit-form';
import { submitDraft } from './kit-form-chrome.story-host';
import { KitNumberInput } from './kit-number-input';
import { KitOption } from './kit-option';
import { KitOptionGroup } from './kit-option-group';
import { KitOrDefault } from './kit-or-default';
import { KitTextInput } from './kit-text-input';

interface Draft {
  slug: string;
  note: string;
  parkTimeoutDefault: boolean;
  parkTimeoutHours: string;
  mode: string;
}

const rules = schema<Draft>((p) => {
  required(p.slug);
  disabled(p.parkTimeoutHours, ({ valueOf }) => valueOf(p.parkTimeoutDefault));
  validate(p.parkTimeoutHours, ({ value, valueOf }) =>
    valueOf(p.parkTimeoutDefault) || /^\d+$/.test(value())
      ? null
      : { kind: 'hours', message: 'Enter whole hours' },
  );
  validate(p, ({ value }) =>
    value().note === 'bad'
      ? { kind: 'root', message: 'Root rule failed' }
      : null,
  );
});

@Component({
  imports: [
    FormField,
    KitForm,
    KitField,
    KitTextInput,
    KitNumberInput,
    KitOrDefault,
    KitOptionGroup,
    KitOption,
  ],
  template: `
    <fleet-kit-form
      [form]="f"
      (save)="save()"
      (cancelled)="cancels = cancels + 1"
    >
      <fleet-kit-field label="Slug" [field]="f.slug" hint="Lowercase">
        <fleet-kit-text-input [formField]="f.slug" />
      </fleet-kit-field>
      <fleet-kit-field label="Note" [field]="f.note">
        <fleet-kit-text-input [formField]="f.note" />
      </fleet-kit-field>
      <fleet-kit-field label="Park timeout" [field]="f.parkTimeoutHours">
        <fleet-kit-or-default
          [formField]="f.parkTimeoutDefault"
          defaultLabel="Default"
        >
          <fleet-kit-number-input [formField]="f.parkTimeoutHours" unit="h" />
        </fleet-kit-or-default>
      </fleet-kit-field>
      <fleet-kit-field label="Mode" [field]="f.mode">
        <fleet-kit-option-group [formField]="f.mode">
          <fleet-kit-option value="a">A</fleet-kit-option>
          <fleet-kit-option value="b">B</fleet-kit-option>
        </fleet-kit-option-group>
      </fleet-kit-field>
    </fleet-kit-form>
  `,
})
class Host {
  saves = 0;
  cancels = 0;
  readonly model = signal<Draft>({
    slug: '',
    note: '',
    parkTimeoutDefault: false,
    parkTimeoutHours: '',
    mode: 'a',
  });
  readonly f = form(this.model, rules);
  save(): void {
    this.saves += 1;
  }
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
  const fields = () => [
    ...root.querySelectorAll<HTMLElement>('fleet-kit-field'),
  ];
  const type = async (input: HTMLInputElement, text: string) => {
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
  };
  return { fixture, host: fixture.componentInstance, root, fields, type };
}

describe('form chrome', () => {
  afterEach(() => (document.body.innerHTML = ''));

  it('marks required fields before input and shows errors only once touched', async () => {
    const { fixture, root, fields } = await mount();
    const [slug, note] = fields();
    expect(slug.querySelector('.req')).not.toBeNull();
    expect(note.querySelector('.req')).toBeNull();
    expect(slug.querySelector('fleet-kit-error-line p')).toBeNull();
    slug.querySelector('input')!.dispatchEvent(new Event('blur'));
    await fixture.whenStable();
    expect(slug.querySelector('fleet-kit-error-line p')).not.toBeNull();
    expect(root.querySelector('[data-testid="kit-form-line"]')).toBeNull();
  });

  it('puts field refusals under their field and form refusals on the form line', async () => {
    const { fixture, host, root, fields } = await mount();
    host.f.slug().value.set('taken');
    host.f.parkTimeoutHours().value.set('4');
    await fixture.whenStable();
    const ok = await submitDraft(host.f, async () => ({
      ok: false,
      slug: 'Slug taken',
      form: 'Hub is unhappy',
    }));
    await fixture.whenStable();
    expect(ok).toBe(false);
    expect(fields()[0].textContent).toContain('Slug taken');
    expect(fields()[0].textContent).not.toContain('Hub is unhappy');
    expect(
      root.querySelector('[data-testid="kit-form-line"]')?.textContent,
    ).toContain('Hub is unhappy');
    expect(
      root.querySelector('[data-testid="kit-form-line"]')?.textContent,
    ).not.toContain('Slug taken');
  });

  it('shows a root rule error on the form line, not under a field', async () => {
    const { fixture, host, root, fields, type } = await mount();
    host.f.slug().value.set('s');
    host.f.parkTimeoutHours().value.set('4');
    await type(fields()[1].querySelector('input')!, 'bad');
    host.f.note().markAsTouched();
    await fixture.whenStable();
    expect(
      root.querySelector('[data-testid="kit-form-line"]')?.textContent,
    ).toContain('Root rule failed');
    expect(
      fields().some((f) => f.textContent?.includes('Root rule failed')),
    ).toBe(false);
  });

  it('emits save once on native submit, cancel without submitting, and disables Save while submitting', async () => {
    const { fixture, host, root } = await mount();
    const nativeForm = root.querySelector('form')!;
    const event = new Event('submit', { cancelable: true });
    nativeForm.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(host.saves).toBe(1);

    root.querySelector<HTMLElement>('[data-testid="kit-form-cancel"]')!.click();
    expect(host.cancels).toBe(1);
    expect(host.saves).toBe(1);

    expect(
      root.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!
        .type,
    ).toBe('submit');
    expect(fixture).toBeTruthy();
  });

  it('disables Save during submission', async () => {
    const { fixture, host, root } = await mount();
    host.f.slug().value.set('x');
    host.f.parkTimeoutHours().value.set('2');
    let release!: () => void;
    const pending = submitDraft(
      host.f,
      () => new Promise((resolve) => (release = () => resolve({ ok: true }))),
    );
    await fixture.whenStable();
    expect(
      root.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!
        .disabled,
    ).toBe(true);
    release();
    await pending;
    await fixture.whenStable();
    expect(
      root.querySelector<HTMLButtonElement>('[data-testid="kit-form-save"]')!
        .disabled,
    ).toBe(false);
  });

  it('keeps number text as typed, and the default toggle disables without losing it', async () => {
    const { fixture, host, root, type } = await mount();
    const num = root.querySelector<HTMLInputElement>(
      'fleet-kit-number-input input',
    )!;
    expect(num.type).toBe('text');
    await type(num, '12x');
    expect(host.model().parkTimeoutHours).toBe('12x');
    await type(num, '1e3');
    expect(host.model().parkTimeoutHours).toBe('1e3');
    expect(host.f.parkTimeoutHours().invalid()).toBe(true);

    root
      .querySelector<HTMLElement>('button[data-testid="kit-or-default-chip"]')!
      .click();
    await fixture.whenStable();
    expect(host.model().parkTimeoutDefault).toBe(true);
    expect(num.disabled).toBe(true);
    expect(num.value).toBe('1e3');
    expect(host.f.parkTimeoutHours().errors()).toEqual([]);

    root
      .querySelector<HTMLElement>('button[data-testid="kit-or-default-chip"]')!
      .click();
    await fixture.whenStable();
    expect(num.disabled).toBe(false);
    expect(num.value).toBe('1e3');

    await type(num, '');
    expect(host.f.parkTimeoutHours().invalid()).toBe(true);
  });

  it('names projected controls by the field label unless they carry their own ariaLabel', async () => {
    const { root, fields } = await mount();
    const [slug] = fields();
    const labelId = slug.querySelector('.label')!.id;
    expect(slug.querySelector('input')!.getAttribute('aria-labelledby')).toBe(
      labelId,
    );
    const group = root.querySelector('fleet-kit-option-group')!;
    expect(group.getAttribute('aria-labelledby')).toBe(
      fields()[3].querySelector('.label')!.id,
    );
    expect(
      root
        .querySelector('fleet-kit-number-input input')!
        .getAttribute('aria-labelledby'),
    ).toBe(fields()[2].querySelector('.label')!.id);
    expect(
      root.querySelector(
        'fleet-kit-field > label, fleet-kit-field > div > label',
      ),
    ).toBeNull();
  });
});
