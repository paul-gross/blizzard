import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type CadenceDraft, FleetCadenceForm } from './cadence-form';
import { EMPTY_CADENCE_DRAFT } from './gardening-cadences-page.model';

describe('FleetCadenceForm', () => {
  async function mount(creating: boolean, initial: CadenceDraft = EMPTY_CADENCE_DRAFT) {
    await TestBed.configureTestingModule({
      imports: [FleetCadenceForm],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetCadenceForm);
    fixture.componentRef.setInput('initial', initial);
    fixture.componentRef.setInput('creating', creating);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const q = (suffix: string) => el.querySelector<HTMLElement>(`[data-testid="gardening-cadence-form-${suffix}"]`);
    const type = async (suffix: string, value: string) => {
      const input = q(suffix) as HTMLInputElement;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await fixture.whenStable();
    };
    const saved: CadenceDraft[] = [];
    fixture.componentInstance.saved.subscribe((draft) => saved.push(draft));
    return { fixture, q, type, saved };
  }

  it('creates from the slug, name, and rhythm typed', async () => {
    const { q, type, saved } = await mount(true);

    await type('slug', 'evening');
    await type('cron', '0 20 * * *');
    await type('zone', 'America/Chicago');
    await type('spacing', '10d');
    expect(q('save')?.textContent).toContain('Create');
    q('save')!.click();

    expect(saved).toEqual([{ ...EMPTY_CADENCE_DRAFT, slug: 'evening', cron: '0 20 * * *', zone: 'America/Chicago', spacing: '10d' }]);
  });

  it('fixes the slug on an edit, opening on the cadence’s own values', async () => {
    const initial = { slug: 'evening', name: 'Evening', cron: '0 20 * * *', zone: 'UTC', spacing: '', anchor: '' };
    const { q, saved } = await mount(false, initial);

    expect(q('slug')).toBeNull();
    expect((q('name') as HTMLInputElement).value).toBe('Evening');
    q('save')!.click();
    expect(saved).toEqual([initial]);
  });

  it('disables Save while pending and shows a refusal', async () => {
    const { fixture, q } = await mount(true);
    fixture.componentRef.setInput('pending', true);
    fixture.componentRef.setInput('error', 'slug already held');
    await fixture.whenStable();

    expect((q('save') as HTMLButtonElement).disabled).toBe(true);
    expect(q('error')?.textContent).toContain('slug already held');
  });
});
