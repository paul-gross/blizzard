import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form, validateTree } from '@angular/forms/signals';

import { awaitingDecision } from 'fleet/testing';
import { type DecisionNoteDraft, EMPTY_DECISION_NOTE_DRAFT } from './awaiting-human.domain';
import { ChunkDecisionFormView } from './chunk-decision-form-view';

@Component({
  selector: 'fleet-decision-form-view-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDecisionFormView],
  template: `<fleet-chunk-decision-form-view [decision]="decision" [form]="noteForm" (choose)="choices.push($event)" />`,
})
class Host {
  readonly decision = awaitingDecision({
    decision_id: 'de_1',
    node_name: 'gate',
    choices: [
      { name: 'approve', description: 'Ship it.', would_override_red_checks: false },
      { name: 'override', description: 'Ship it anyway.', would_override_red_checks: true },
    ],
  });
  readonly rootError = signal<string | null>(null);
  readonly noteForm = form(signal<DecisionNoteDraft>(EMPTY_DECISION_NOTE_DRAFT), (p) => {
    validateTree(p, ({ fieldTree }) => {
      const message = this.rootError();
      return message === null ? [] : [{ kind: 'hub', fieldTree, message }];
    });
  });
  readonly choices: string[] = [];
}

describe('ChunkDecisionFormView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
  });

  it('binds the note to the field and emits each picked choice as an action, marking an override', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const host = fixture.componentInstance;

    const input = el.querySelector<HTMLInputElement>('[data-testid="decision-note-input"]')!;
    input.value = 'careful';
    input.dispatchEvent(new Event('input'));
    expect(host.noteForm.note().value()).toBe('careful');

    const chips = el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]');
    expect(chips[1].textContent).toContain('overrides red checks');
    chips[1].click();
    expect(host.choices).toEqual(['override']);
  });

  it('shows the root errors on the form line', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.rootError.set('decision de_1 is closed');
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="decision-form-error"]')?.textContent).toContain(
      'is closed',
    );
  });
});
