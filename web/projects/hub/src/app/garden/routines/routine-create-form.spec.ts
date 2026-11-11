import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { UrgencyView } from 'fleet';
import { RoutineCreateForm, type RoutineCreateDraft } from './routine-create-form';

describe('routine create form urgency', () => {
  it('starts at the tenant default and emits an explicit selection', async () => {
    await TestBed.configureTestingModule({ imports: [RoutineCreateForm], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(RoutineCreateForm);
    fixture.componentRef.setInput('urgencies', [
      { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
      { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
    ]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const values: RoutineCreateDraft[] = [];
    fixture.componentInstance.saved.subscribe((value) => values.push(value));
    const trigger = root.querySelector<HTMLElement>('[data-testid="routine-create-urgency"]')!;
    expect(trigger.textContent).toContain('Tenant default (Normal)');
    root.querySelector<HTMLElement>('[data-testid="routine-create-save"]')!.click();
    expect(values[0].urgency).toBe('');
    trigger.click();
    await fixture.whenStable();
    const urgent = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Urgent')!;
    urgent.click();
    await fixture.whenStable();
    root.querySelector<HTMLElement>('[data-testid="routine-create-save"]')!.click();
    expect(values[1].urgency).toBe('urgent');
    fixture.destroy();
  });
});
