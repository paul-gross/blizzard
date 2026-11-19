import { Component, input, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { type CadenceDraft, EMPTY_CADENCE_DRAFT } from '../core/cadence.domain';
import { cadenceCreateRules } from './cadence.form';
import { FleetCadenceForm } from './cadence-form';

@Component({
  selector: 'app-test-cadence-form-host',
  imports: [FleetCadenceForm],
  template: `<app-cadence-form [form]="f" [creating]="creating()" (save)="saves = saves + 1" (cancel)="cancels = cancels + 1" />`,
})
class Host {
  readonly creating = input(false);
  readonly draft = signal<CadenceDraft>(EMPTY_CADENCE_DRAFT);
  readonly f = form(this.draft, cadenceCreateRules);
  saves = 0;
  cancels = 0;
}

describe('FleetCadenceForm', () => {
  async function mount(creating: boolean, initial: CadenceDraft = EMPTY_CADENCE_DRAFT) {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    fixture.componentRef.setInput('creating', creating);
    fixture.componentInstance.draft.set(initial);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const q = (suffix: string) => el.querySelector<HTMLElement>(`[data-testid="gardening-cadence-form-${suffix}"]`);
    const type = async (suffix: string, value: string) => {
      const field = q(suffix) as HTMLInputElement;
      field.value = value;
      field.dispatchEvent(new Event('input', { bubbles: true }));
      await fixture.whenStable();
    };
    /** The zone box is pick-only: search the list, then click the matching row. */
    const pickZone = async (zone: string) => {
      await type('zone', zone);
      const row = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find(
        (r) => r.querySelector('.label')?.textContent?.trim() === zone,
      );
      row!.click();
      await fixture.whenStable();
    };
    return { fixture, host: fixture.componentInstance, el, q, type, pickZone };
  }

  it('binds every field typed into the draft it is handed, and emits save', async () => {
    const { fixture, host, q, type, pickZone } = await mount(true);

    await type('slug', 'evening');
    await type('cron', '0 20 * * *');
    await pickZone('America/Chicago');
    await type('spacing', '10d');
    expect(q('save')?.textContent).toContain('Create');
    q('save')!.click();
    await fixture.whenStable();

    expect(host.draft()).toEqual({ ...EMPTY_CADENCE_DRAFT, slug: 'evening', cron: '0 20 * * *', zone: 'America/Chicago', spacing: '10d' });
    expect(host.saves).toBe(1);
  });

  it('fixes the slug on an edit, opening on the draft it is handed', async () => {
    const initial: CadenceDraft = { slug: 'evening', name: 'Evening', cron: '0 20 * * *', zone: 'UTC', spacing: '', anchor: 'unstated' };
    const { q } = await mount(false, initial);

    expect(q('slug')).toBeNull();
    expect((q('name') as HTMLInputElement).value).toBe('Evening');
    expect(q('save')?.textContent).toContain('Save');
  });

  it('marks the slug required on create', async () => {
    const { q } = await mount(true);
    expect(q('slug')!.closest('fleet-kit-field')!.querySelector('.req')).not.toBeNull();
  });

  it('shows a rule failure under its field once touched', async () => {
    const { fixture, host, q, type } = await mount(false, { ...EMPTY_CADENCE_DRAFT, spacing: '1d' });

    await type('spacing', '10');
    const field = q('spacing')!.closest('fleet-kit-field')!;
    expect(field.textContent).not.toContain('A whole number above zero');
    host.f.spacing().markAsTouched();
    await fixture.whenStable();
    expect(field.textContent).toContain('A whole number above zero');
  });

  it('emits cancel', async () => {
    const { host, q } = await mount(true);
    q('cancel')!.click();
    expect(host.cancels).toBe(1);
  });
});
