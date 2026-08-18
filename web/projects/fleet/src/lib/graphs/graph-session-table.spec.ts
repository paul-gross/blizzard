import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphSessionView } from '../api/hub';
import { GraphSessionTable } from './graph-session-table';

const SESSIONS: readonly GraphSessionView[] = [
  {
    name: 'planning',
    model: ['blizzard:advanced'],
    effort: 'high',
  },
  {
    name: 'code',
    model: ['blizzard:basic', 'gpt-5.3-codex'],
    effort: 'medium',
    compaction_window: '150000',
    rotate: { max_context_tokens: 120000, max_invocations: 30 },
  },
  {
    name: 'gate',
    model: [],
  },
];

describe('GraphSessionTable', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphSessionTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one row per declaration, with the model list, effort, compaction window, and declared bounds', async () => {
    const fixture = TestBed.createComponent(GraphSessionTable);
    fixture.componentRef.setInput('sessions', SESSIONS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="graph-detail-session-row"]');
    expect(rows).toHaveLength(3);

    const code = el.querySelector('[data-session-name="code"]') as HTMLElement;
    expect(code.textContent).toContain('blizzard:basic, gpt-5.3-codex');
    expect(code.textContent).toContain('medium');
    expect(code.textContent).toContain('150000');
    // Only the two thresholds the declaration actually set, and `max_invocations`
    // labelled as invocations rather than node-steps.
    expect(code.textContent).toContain('120000 ctx tokens');
    expect(code.textContent).toContain('30 invocations');
    expect(code.textContent).not.toContain('transcript bytes');
  });

  it('dashes an undeclared model list, effort, compaction window, and rotate policy', async () => {
    const fixture = TestBed.createComponent(GraphSessionTable);
    fixture.componentRef.setInput('sessions', SESSIONS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const gate = el.querySelector('[data-session-name="gate"]') as HTMLElement;
    expect(gate.textContent).toContain('—');

    const planning = el.querySelector('[data-session-name="planning"]') as HTMLElement;
    expect(planning.textContent).toContain('blizzard:advanced');
    expect(planning.textContent).toContain('high');
  });

  it('renders nothing at all for a graph that declares no sessions', async () => {
    // Every graph minted before #144. An empty table under a "Sessions" heading would
    // read as missing data rather than as "this graph declares none".
    const fixture = TestBed.createComponent(GraphSessionTable);
    fixture.componentRef.setInput('sessions', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-detail-sessions"]')).toBeNull();
  });
});
