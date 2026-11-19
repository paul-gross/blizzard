import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form, validateTree } from '@angular/forms/signals';

import { awaitingQuestion } from 'fleet/testing';
import { type AnswerDraft, EMPTY_ANSWER_DRAFT } from './awaiting-human.domain';
import { ChunkQuestionFormView } from './chunk-question-form-view';

@Component({
  selector: 'fleet-question-form-view-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkQuestionFormView],
  template: `<fleet-chunk-question-form-view [question]="question" [form]="answerForm" (answer)="answers.push(true)" (pick)="picks.push($event)" />`,
})
class Host {
  readonly question = awaitingQuestion({ question_id: 'qn_1', options: ['rest', 'graphql'] });
  readonly rootError = signal<string | null>(null);
  readonly answerForm = form(signal<AnswerDraft>(EMPTY_ANSWER_DRAFT), (p) => {
    validateTree(p, ({ fieldTree }) => {
      const message = this.rootError();
      return message === null ? [] : [{ kind: 'hub', fieldTree, message }];
    });
  });
  readonly answers: true[] = [];
  readonly picks: string[] = [];
}

describe('ChunkQuestionFormView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
  });

  it('binds the typed answer to the field and emits Answer and each picked option as actions', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const host = fixture.componentInstance;

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    input.dispatchEvent(new Event('input'));
    expect(host.answerForm.answer().value()).toBe('rest');

    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')!.click();
    el.querySelectorAll<HTMLButtonElement>('[data-testid="question-option"]')[1].click();
    expect(host.answers).toHaveLength(1);
    expect(host.picks).toEqual(['graphql']);
    expect(el.querySelector('[role="toolbar"]')).not.toBeNull();
  });

  it('shows the root errors on the form line', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.rootError.set('question qn_1 is closed');
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="answer-form-error"]')?.textContent).toContain(
      'is closed',
    );
  });
});
