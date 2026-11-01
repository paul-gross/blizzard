import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type ConfigFieldDef, type ConfigFormValues } from './config-edit.model';
import { ConfigFormDialog, type ConfigFormMode } from './config-form-dialog';

const FIELDS: readonly ConfigFieldDef[] = [
  { key: 'name', label: 'Name', kind: 'text', required: true, createOnly: true },
  { key: 'owner', label: 'Owner', kind: 'text', required: true },
  { key: 'value', label: 'Value', kind: 'password' },
];
const SHOWN: ConfigFormValues = { name: 'blizzard', owner: 'paul', value: '' };

describe('ConfigFormDialog', () => {
  async function mount(mode: ConfigFormMode, submitError: string | null = null) {
    await TestBed.configureTestingModule({
      imports: [ConfigFormDialog],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ConfigFormDialog);
    fixture.componentRef.setInput('heading', 'Edit');
    fixture.componentRef.setInput('mode', mode);
    fixture.componentRef.setInput('fields', FIELDS);
    fixture.componentRef.setInput('initial', mode === 'edit' ? SHOWN : null);
    fixture.componentRef.setInput('submitLabel', 'Save');
    fixture.componentRef.setInput('submitError', submitError);
    fixture.componentRef.setInput('testidPrefix', 'dlg');
    await fixture.whenStable();
    return fixture;
  }

  const el = (fixture: { nativeElement: HTMLElement }, id: string) =>
    document.querySelector<HTMLElement>(`[data-testid="${id}"]`) ??
    fixture.nativeElement.querySelector(`[data-testid="${id}"]`);

  async function type(fixture: Awaited<ReturnType<typeof mount>>, id: string, value: string) {
    const input = el(fixture, id)!.querySelector('input') ?? (el(fixture, id) as HTMLInputElement);
    input.value = value;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
  }

  it('edit: Save is held off until a field differs, then the diff names the change', async () => {
    const fixture = await mount('edit');
    const save = () => (el(fixture, 'dlg-submit') as HTMLButtonElement).disabled;
    expect(save()).toBe(true);
    expect(el(fixture, 'dlg-diff-empty')).not.toBeNull();
    expect(el(fixture, 'dlg-field-name')).toBeNull();

    await type(fixture, 'dlg-field-owner', 'ana');

    expect(save()).toBe(false);
    expect(el(fixture, 'dlg-diff')!.textContent).toContain('Owner');
    expect(el(fixture, 'dlg-diff')!.textContent).toContain('ana');
  });

  it('create: Create waits for the required fields and emits the values', async () => {
    const fixture = await mount('create');
    const emitted: ConfigFormValues[] = [];
    fixture.componentInstance.submitted.subscribe((values) => emitted.push(values));
    const submit = () => el(fixture, 'dlg-submit') as HTMLButtonElement;
    expect(submit().disabled).toBe(true);
    expect(el(fixture, 'dlg-diff')).toBeNull();

    await type(fixture, 'dlg-field-name', 'x');
    await type(fixture, 'dlg-field-owner', 'o');
    submit().click();

    expect(emitted).toEqual([{ name: 'x', owner: 'o', value: '' }]);
  });

  it('renders a password field as a password input', async () => {
    const fixture = await mount('create');
    const input =
      el(fixture, 'dlg-field-value')!.querySelector('input') ?? (el(fixture, 'dlg-field-value') as HTMLInputElement);
    expect(input.getAttribute('type')).toBe('password');
  });

  it('toggles a flag through the kit checkbox and submits its boolean value', async () => {
    const fixture = await mount('create');
    fixture.componentRef.setInput('fields', [{ key: 'annotate', label: 'Annotates', kind: 'checkbox' }]);
    await fixture.whenStable();
    expect(fixture.nativeElement.querySelector('fleet-kit-checkbox')).not.toBeNull();
    const flag = el(fixture, 'dlg-field-annotate') as HTMLInputElement;
    const emitted: ConfigFormValues[] = [];
    fixture.componentInstance.submitted.subscribe((values) => emitted.push(values));

    expect(flag.checked).toBe(false);
    flag.click();
    await fixture.whenStable();
    expect(flag.checked).toBe(true);
    (el(fixture, 'dlg-submit') as HTMLButtonElement).click();
    expect(emitted).toEqual([{ annotate: true }]);
  });

  it('offers enabled forge choices through the kit select, filtered when the provider changes', async () => {
    const fixture = await mount('create');
    fixture.componentRef.setInput('fields', [
      { key: 'provider', label: 'Provider', kind: 'text', required: true },
      { key: 'forge', label: 'Forge', kind: 'select', required: true, filterBy: 'provider', options: [
        { value: 'frg_github', label: 'GitHub', kind: 'github' },
        { value: 'frg_gitlab', label: 'GitLab', kind: 'gitlab' },
      ] },
    ]);
    await fixture.whenStable();
    await type(fixture, 'dlg-field-provider', 'github');
    const select = el(fixture, 'dlg-field-forge') as HTMLButtonElement;
    select.click();
    await fixture.whenStable();
    expect(document.querySelectorAll('[role="option"]')).toHaveLength(1);
    expect(document.querySelector('[role="option"]')?.textContent).toContain('GitHub');
    (document.querySelector('[role="option"]') as HTMLElement).click();
    await fixture.whenStable();
    expect((el(fixture, 'dlg-submit') as HTMLButtonElement).disabled).toBe(false);
    await type(fixture, 'dlg-field-provider', 'gitlab');
    expect((el(fixture, 'dlg-submit') as HTMLButtonElement).disabled).toBe(true);
  });

  it('edit: a refetched initial reaches untouched fields after a failed save', async () => {
    const fixture = await mount('edit', 'revision conflict');
    const emitted: ConfigFormValues[] = [];
    fixture.componentInstance.submitted.subscribe((values) => emitted.push(values));
    await type(fixture, 'dlg-field-owner', 'ana');

    fixture.componentRef.setInput('initial', { ...SHOWN, name: 'renamed-elsewhere' });
    await fixture.whenStable();

    expect(el(fixture, 'dlg-diff')!.textContent).not.toContain('renamed-elsewhere');
    (el(fixture, 'dlg-submit') as HTMLButtonElement).click();
    expect(emitted).toEqual([{ name: 'renamed-elsewhere', owner: 'ana', value: '' }]);
  });

  it('shows a gated number field only while its gate holds, and holds off a non-whole number', async () => {
    const fixture = await mount('create');
    fixture.componentRef.setInput('fields', [
      { key: 'policy', label: 'Policy', kind: 'select', options: [
        { value: 'merge', label: 'merge' },
        { value: 'park', label: 'park' },
        { value: 'ff', label: 'ff — forge lacks ref-update', disabled: true },
      ] },
      { key: 'hours', label: 'Hours', kind: 'number', shownWhen: { key: 'policy', value: 'park' } },
    ]);
    await fixture.whenStable();
    const values: ConfigFormValues[] = [];
    fixture.componentInstance.valuesChange.subscribe((v) => values.push(v));
    expect(el(fixture, 'dlg-field-hours')).toBeNull();

    (el(fixture, 'dlg-field-policy') as HTMLButtonElement).click();
    await fixture.whenStable();
    const options = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'));
    expect(options[2].getAttribute('aria-disabled')).toBe('true');
    expect(options[2].getAttribute('title')).toContain('lacks ref-update');
    options[1].click();
    await fixture.whenStable();
    expect(values.at(-1)).toMatchObject({ policy: 'park' });
    expect(el(fixture, 'dlg-field-hours')).not.toBeNull();

    await type(fixture, 'dlg-field-hours', '1.5');
    expect((el(fixture, 'dlg-submit') as HTMLButtonElement).disabled).toBe(true);
    await type(fixture, 'dlg-field-hours', '2');
    expect((el(fixture, 'dlg-submit') as HTMLButtonElement).disabled).toBe(false);
  });

  it('shows the failed write inline', async () => {
    const fixture = await mount('edit', 'stale revision');
    expect(el(fixture, 'dlg-submit-error')!.textContent).toContain('stale revision');
  });
});
