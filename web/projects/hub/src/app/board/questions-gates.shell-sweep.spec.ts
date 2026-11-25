import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { GatesPanelView } from './gates/gates-view';
import { QuestionsPanelView } from './questions/questions-view';

describe('questions and gates views shell sweep', () => {
  it('renders domain questions and gates within their columns at desktop and phone widths', async () => {
    await TestBed.configureTestingModule({
      imports: [QuestionsPanelView, GatesPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    const questions = TestBed.createComponent(QuestionsPanelView);
    const gates = TestBed.createComponent(GatesPanelView);
    questions.componentRef.setInput('state', 'ready');
    questions.componentRef.setInput('questions', [{
      question_id: 'qn_01', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      question: 'Which migration strategy should we use?\n\nConsider the existing data.',
      options: ['incremental migration', 'single pass'], runner_id: 'rn_01', runner_name: 'runner-alpha',
    }]);
    gates.componentRef.setInput('state', 'ready');
    gates.componentRef.setInput('gates', [{
      decision_id: 'dc_01', chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
      node_name: 'approval-gate', imposed_by_runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
      imposed_by_runner_name: 'runner-alpha', choices: ['approve', 'reject'],
    }]);
    const roots = [questions.nativeElement as HTMLElement, gates.nativeElement as HTMLElement];
    roots.forEach((root) => document.body.appendChild(root));
    try {
      await questions.whenStable();
      await gates.whenStable();
      for (const width of [1280, 390, 320]) {
        await page.viewport(width, 800);
        for (const root of roots) root.style.width = `${Math.min(width, 390)}px`;
        questions.detectChanges();
        gates.detectChanges();
        const question = roots[0].querySelector<HTMLElement>('[data-testid="rail-question"]')!;
        const gate = roots[1].querySelector<HTMLElement>('[data-testid="rail-gate"]')!;
        expect(question.textContent).toContain('incremental migration');
        expect(gate.textContent).toContain('approval-gate');
        expect(gate.textContent).toContain('runner-alpha');
        for (const [root, row] of [[roots[0], question], [roots[1], gate]]) {
          expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
          expect(row.getBoundingClientRect().right).toBeLessThanOrEqual(root.getBoundingClientRect().right);
        }
      }
    } finally {
      roots.forEach((root) => root.remove());
    }
  });
});
