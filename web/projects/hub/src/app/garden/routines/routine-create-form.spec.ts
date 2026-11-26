import { Component, input, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import type { Urgency } from 'fleet/core';
import { EMPTY_ROUTINE_CREATE_DRAFT, type RoutineCreateDraft } from '../core/routine.domain';
import { RoutineCreateForm } from './routine-create-form';
import { routineCreateRules } from './routine.form';

@Component({
  selector: 'app-test-routine-create-form-host',
  imports: [RoutineCreateForm],
  template: `<app-routine-create-form [form]="f" [urgencies]="urgencies()" (save)="saves = saves + 1" (cancel)="cancels = cancels + 1" />`,
})
class Host {
  readonly urgencies = input<readonly Urgency[]>([]);
  readonly draft = signal<RoutineCreateDraft>(EMPTY_ROUTINE_CREATE_DRAFT);
  readonly f = form(this.draft, routineCreateRules);
  saves = 0;
  cancels = 0;
}

describe('RoutineCreateForm urgency', () => {
  it('starts at the tenant default and writes an explicit selection into the draft', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    fixture.componentRef.setInput('urgencies', [
      { id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as Urgency,
      { id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as Urgency,
    ]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const trigger = root.querySelector<HTMLElement>('[data-testid="gardening-routine-create-urgency"]')!;
    expect(trigger.textContent).toContain('Tenant default (Normal)');
    expect(fixture.componentInstance.draft().urgency).toBe('');
    trigger.click();
    await fixture.whenStable();
    const urgent = [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Urgent')!;
    urgent.click();
    await fixture.whenStable();
    expect(fixture.componentInstance.draft().urgency).toBe('urgent');
    root.querySelector<HTMLElement>('[data-testid="gardening-routine-create-save"]')!.click();
    expect(fixture.componentInstance.saves).toBe(1);
    fixture.destroy();
  });
});
