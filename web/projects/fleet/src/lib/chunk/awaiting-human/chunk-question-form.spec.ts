import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { awaitingQuestion, recordingAwaitingHumanActions } from 'fleet/testing';
import { ChunkQuestionForm } from './chunk-question-form';

async function mount(port: ReturnType<typeof recordingAwaitingHumanActions>) {
  await TestBed.configureTestingModule({
    imports: [ChunkQuestionForm],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkQuestionForm);
  fixture.componentRef.setInput('chunkId', 'ch_1');
  fixture.componentRef.setInput('question', awaitingQuestion({ question_id: 'qn_1', options: ['rest'] }));
  fixture.componentRef.setInput('actions', port.actions);
  await fixture.whenStable();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

async function settled(fixture: { whenStable(): Promise<unknown> }): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await fixture.whenStable();
}

describe('ChunkQuestionForm', () => {
  it('saves the typed answer for its chunk and question', async () => {
    const port = recordingAwaitingHumanActions();
    const { fixture, el } = await mount(port);

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'graphql';
    input.dispatchEvent(new Event('input'));
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')!.click();
    await settled(fixture);

    expect(port.answers).toEqual([{ chunkId: 'ch_1', questionId: 'qn_1', draft: { answer: 'graphql' } }]);
  });

  it('sends nothing while its local rule fails', async () => {
    const port = recordingAwaitingHumanActions();
    const { fixture, el } = await mount(port);

    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')!.click();
    await settled(fixture);

    expect(port.answers).toEqual([]);
    expect(el.textContent).toContain('Required');
  });

  it('saves a picked option without leaving the untouched field showing its rule', async () => {
    const port = recordingAwaitingHumanActions();
    const { fixture, el } = await mount(port);

    el.querySelector<HTMLButtonElement>('[data-testid="question-option"]')!.click();
    await settled(fixture);

    expect(port.answers.map((call) => call.draft)).toEqual([{ answer: 'rest' }]);
    expect(el.textContent).not.toContain('Required');
  });
});
