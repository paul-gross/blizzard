import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle } from '../testing/settle';
import { type HubClientStub, stubHubClient } from '../testing/stub-hub-client';
import { QuestionsPanel } from './questions-panel';

const QUESTIONS = [
  {
    question_id: 'qn_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    question: 'Which API style should the endpoint use?',
    options: ['rest', 'graphql'],
    epoch: 1,
    runner_id: 'rn_01',
    asked_at: '2026-07-16T00:00:01Z',
    answered: false,
  },
  {
    question_id: 'qn_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    question: 'Should the migration backfill in one pass?',
    epoch: 2,
    runner_id: 'rn_02',
    asked_at: '2026-07-16T00:00:02Z',
    answered: false,
  },
];

describe('QuestionsPanel', () => {
  let stub: HubClientStub;

  const render = async (questions: unknown = QUESTIONS) => {
    stub = stubHubClient((method, path) => (method === 'GET' && path === '/api/questions' ? questions : {}));
    await TestBed.configureTestingModule({
      imports: [QuestionsPanel],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(QuestionsPanel);
    await settle(fixture);
    return fixture;
  };

  afterEach(() => stub.restore());

  it('lists every open ask across the fleet, not just the selected chunk (MVP criterion 7)', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    // The rail reads the fleet-wide surface, so an ask on a chunk nobody has opened
    // still shows — that is the whole reason it does not ride on the chunk's detail.
    expect(stub.forRoute('/api/questions', 'GET').length).toBeGreaterThan(0);
    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(2);
    expect(el.querySelector('[data-testid="questions-count"]')?.textContent).toContain('2');

    const first = el.querySelectorAll('[data-testid="rail-question"]')[0];
    expect(first.querySelector('[data-testid="rail-question-text"]')?.textContent).toContain('Which API style');
    // The chunk is named the way the board names it, so the two surfaces agree.
    expect(first.querySelector('[data-testid="rail-question-chunk"]')?.textContent).toContain('ch_…3YJ9');
    expect(first.querySelector('[data-testid="rail-question-options"]')?.textContent).toContain('rest · graphql');
  });

  it('omits the options line for an ask that offers none', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const second = el.querySelectorAll('[data-testid="rail-question"]')[1];
    expect(second.querySelector('[data-testid="rail-question-options"]')).toBeNull();
  });

  it('emits the chunk id when an ask is activated — the answer is given in the dock', async () => {
    const fixture = await render();
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="rail-question"]')?.click();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('rests on an empty state when the fleet has no open ask', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="questions-empty"]')).not.toBeNull();
    expect(el.querySelectorAll('[data-testid="rail-question"]')).toHaveLength(0);
    // The count badge stays blank rather than printing a bare 0 beside "open questions".
    expect(el.querySelector('[data-testid="questions-count"]')?.textContent?.trim()).toBe('');
  });
});
