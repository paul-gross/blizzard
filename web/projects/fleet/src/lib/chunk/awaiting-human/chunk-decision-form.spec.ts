import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { awaitingDecision, recordingAwaitingHumanActions } from 'fleet/testing';
import { ChunkDecisionForm } from './chunk-decision-form';

describe('ChunkDecisionForm', () => {
  it('resolves its gate with the picked choice and the note, keeping the note when refused', async () => {
    const port = recordingAwaitingHumanActions({
      resolve: async () => ({ ok: false, fields: { note: 'too long' }, form: null }),
    });
    await TestBed.configureTestingModule({
      imports: [ChunkDecisionForm],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(ChunkDecisionForm);
    fixture.componentRef.setInput('chunkId', 'ch_1');
    fixture.componentRef.setInput('decision', awaitingDecision({ decision_id: 'de_1' }));
    fixture.componentRef.setInput('actions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="decision-note-input"]')!;
    input.value = 'careful';
    input.dispatchEvent(new Event('input'));
    el.querySelector<HTMLButtonElement>('[data-testid="decision-choice"]')!.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();

    expect(port.resolves).toEqual([
      { chunkId: 'ch_1', decisionId: 'de_1', choice: 'approve', draft: { note: 'careful' } },
    ]);
    expect(input.value).toBe('careful');
    expect(el.querySelector('fleet-kit-field')?.textContent).toContain('too long');
  });
});
