import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import type { TranscriptTurn } from './transcript-turn';
import { TranscriptViewer } from './transcript-viewer';

async function render(turns: TranscriptTurn[]): Promise<{ el: HTMLElement; fixture: ComponentFixture<TranscriptViewer> }> {
  await TestBed.configureTestingModule({
    imports: [TranscriptViewer],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(TranscriptViewer);
  fixture.componentRef.setInput('turns', turns);
  await fixture.whenStable();
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

describe('TranscriptViewer', () => {
  it('renders env/asst/tool turns in order, kind-classed', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'env',
        timestamp: '2026-07-16T11:00:00+00:00',
        text: 'NODE ENVELOPE',
        tool: null,
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
      {
        index: 1,
        kind: 'asst',
        timestamp: '2026-07-16T11:00:05+00:00',
        text: 'Starting.',
        tool: null,
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
      {
        index: 2,
        kind: 'tool',
        timestamp: '2026-07-16T11:00:10+00:00',
        text: '',
        tool: {
          name: 'Bash',
          input: { command: 'pytest' },
          input_unparsed: null,
          input_shape: 'object',
          tool_use_id: 't1',
          output: null,
          output_truncated: false,
        },
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
    ]);

    const turns = el.querySelectorAll('[data-testid="transcript-turn"]');
    expect(turns).toHaveLength(3);
    expect(turns[0].classList.contains('k-env')).toBe(true);
    expect(turns[0].textContent).toContain('NODE ENVELOPE');
    expect(turns[1].classList.contains('k-asst')).toBe(true);
    expect(turns[1].textContent).toContain('Starting.');
    expect(turns[2].classList.contains('k-tool')).toBe(true);
    expect(turns[2].textContent).toContain('Bash');
    expect(turns[2].textContent).toContain('"command":"pytest"');
    expect(turns[2].textContent).toContain('running…');
  });

  it('caps a tool call’s rendered input preview rather than dumping the whole structured value (review:F8)', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'tool',
        timestamp: null,
        text: '',
        tool: {
          name: 'Write',
          input: { content: 'x'.repeat(5000) },
          input_unparsed: null,
          input_shape: 'object',
          tool_use_id: 't1',
          output: null,
          output_truncated: false,
        },
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
    ]);

    const preview = el.querySelector('.tc-input');
    expect(preview?.textContent?.length).toBeLessThan(400);
    expect(preview?.textContent).toContain('…');
  });

  it('renders a thinking turn collapsed by default, expanding in place', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'thinking',
        timestamp: '2026-07-16T11:00:00+00:00',
        text: 'considering the options',
        tool: null,
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
    ]);

    const details = el.querySelector('.thinking') as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(details.textContent).toContain('considering the options');

    details.open = true;
    expect(details.open).toBe(true);
  });

  it('shows a presence placeholder, not prose, for a redacted thinking turn', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'thinking',
        timestamp: null,
        text: '',
        tool: null,
        thinking_redacted: true,
        sidechain: null,
        truncated: false,
      },
    ]);

    expect(el.querySelector('.th-redacted')?.textContent).toContain('redacted');
    expect(el.querySelector('.th-body')).toBeNull();
  });

  it('nests a sidechain under its spawning tool call', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'tool',
        timestamp: '2026-07-16T11:00:00+00:00',
        text: '',
        tool: {
          name: 'Task',
          input: { prompt: 'find X' },
          input_unparsed: null,
          input_shape: 'object',
          tool_use_id: 't1',
          output: 'done',
          output_truncated: false,
        },
        thinking_redacted: false,
        sidechain: {
          agent_id: 'agent-1',
          agent_type: 'explorer',
          link: 'prompt-timestamp',
          turns: [
            {
              index: 0,
              kind: 'asst',
              timestamp: '2026-07-16T11:00:01+00:00',
              text: 'looking around',
              tool: null,
              thinking_redacted: false,
              sidechain: null,
              truncated: false,
            },
          ],
        },
        truncated: false,
      },
    ]);

    const nested = el.querySelector('[data-testid="transcript-sidechain-nested"]');
    expect(nested).not.toBeNull();
    expect(nested?.textContent).toContain('explorer');
    expect(nested?.textContent).toContain('looking around');
    expect(el.querySelector('[data-testid="transcript-sidechain-standalone"]')).toBeNull();
  });

  it('gives a nested sidechain the same open-standalone control as an unlinked one (review:F3)', async () => {
    const turn: TranscriptTurn = {
      index: 0,
      kind: 'tool',
      timestamp: '2026-07-16T11:00:00+00:00',
      text: '',
      tool: {
        name: 'Task',
        input: { prompt: 'find X' },
        input_unparsed: null,
        input_shape: 'object',
        tool_use_id: 't1',
        output: 'done',
        output_truncated: false,
      },
      thinking_redacted: false,
      sidechain: {
        agent_id: 'agent-1',
        agent_type: 'explorer',
        link: 'prompt-timestamp',
        turns: [
          {
            index: 0,
            kind: 'asst',
            timestamp: '2026-07-16T11:00:01+00:00',
            text: 'looking around',
            tool: null,
            thinking_redacted: false,
            sidechain: null,
            truncated: false,
          },
        ],
      },
      truncated: false,
    };
    const { el, fixture } = await render([turn]);
    const emitted: TranscriptTurn[] = [];
    fixture.componentInstance.openStandalone.subscribe((t) => emitted.push(t));

    const openButton = el.querySelector<HTMLButtonElement>(
      '[data-testid="transcript-sidechain-nested"] [data-testid="transcript-sidechain-open"]',
    );
    expect(openButton).not.toBeNull();
    openButton?.click();

    expect(emitted).toEqual([turn]);
  });

  it('forwards a nested viewer\'s openStandalone emission through the recursive instance, not swallowing it', async () => {
    const innerSidechainTurn: TranscriptTurn = {
      index: 0,
      kind: 'sidechain',
      timestamp: null,
      text: '',
      tool: null,
      thinking_redacted: false,
      sidechain: { agent_id: null, agent_type: null, link: 'unlinked', turns: [] },
      truncated: false,
    };
    const outerTurn: TranscriptTurn = {
      index: 0,
      kind: 'tool',
      timestamp: null,
      text: '',
      tool: {
        name: 'Task',
        input: {},
        input_unparsed: null,
        input_shape: 'object',
        tool_use_id: 't1',
        output: null,
        output_truncated: false,
      },
      thinking_redacted: false,
      sidechain: { agent_id: null, agent_type: null, link: 'prompt-timestamp', turns: [innerSidechainTurn] },
      truncated: false,
    };
    const { el, fixture } = await render([outerTurn]);
    const emitted: TranscriptTurn[] = [];
    fixture.componentInstance.openStandalone.subscribe((t) => emitted.push(t));

    const innerOpenButton = el.querySelector<HTMLButtonElement>(
      '[data-testid="transcript-sidechain-standalone"] [data-testid="transcript-sidechain-open"]',
    );
    expect(innerOpenButton).not.toBeNull();
    innerOpenButton?.click();

    expect(emitted).toEqual([innerSidechainTurn]);
  });

  it('renders an unlinked sidechain as its own top-level entry', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'sidechain',
        timestamp: null,
        text: '',
        tool: null,
        thinking_redacted: false,
        sidechain: {
          agent_id: null,
          agent_type: null,
          link: 'unlinked',
          turns: [
            {
              index: 0,
              kind: 'asst',
              timestamp: '2026-07-16T11:00:01+00:00',
              text: 'subagent chatter',
              tool: null,
              thinking_redacted: false,
              sidechain: null,
              truncated: false,
            },
          ],
        },
        truncated: false,
      },
    ]);

    const standalone = el.querySelector('[data-testid="transcript-sidechain-standalone"]');
    expect(standalone).not.toBeNull();
    expect(standalone?.textContent).toContain('unlinked');
    expect(standalone?.textContent).toContain('subagent chatter');
  });

  it('emits openStandalone with the sidechain turn when its header is clicked (D7)', async () => {
    const turn: TranscriptTurn = {
      index: 3,
      kind: 'sidechain',
      timestamp: null,
      text: '',
      tool: null,
      thinking_redacted: false,
      sidechain: { agent_id: null, agent_type: null, link: 'unlinked', turns: [] },
      truncated: false,
    };
    const { el, fixture } = await render([turn]);
    const emitted: TranscriptTurn[] = [];
    fixture.componentInstance.openStandalone.subscribe((t) => emitted.push(t));

    (el.querySelector('[data-testid="transcript-sidechain-open"]') as HTMLButtonElement).click();

    expect(emitted).toEqual([turn]);
  });

  it('shows the truncation note on a turn that lost content', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'asst',
        timestamp: null,
        text: 'cut off',
        tool: null,
        thinking_redacted: false,
        sidechain: null,
        truncated: true,
      },
    ]);

    expect(el.querySelector('.trunc-note')?.textContent).toContain('truncated');
  });
});
