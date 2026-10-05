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

  it('shows the failed write inline', async () => {
    const fixture = await mount('edit', 'stale revision');
    expect(el(fixture, 'dlg-submit-error')!.textContent).toContain('stale revision');
  });
});
