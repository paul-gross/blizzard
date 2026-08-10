import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { TranscriptTurn } from './transcript-turn';
import { type SidechainOpenEvent, TranscriptViewer } from './transcript-viewer';

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

  it('renders the tool output once it resolves, replacing the running placeholder (review:F8)', async () => {
    const { el } = await render([
      {
        index: 0,
        kind: 'tool',
        timestamp: null,
        text: '',
        tool: {
          name: 'Bash',
          input: { command: 'pytest' },
          input_unparsed: null,
          input_shape: 'object',
          tool_use_id: 't1',
          output: 'ok — 12 passed',
          output_truncated: false,
        },
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
    ]);

    const output = el.querySelector('.tc-out');
    expect(output?.textContent).toContain('ok — 12 passed');
    expect(output?.textContent).not.toContain('running…');
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

  it('renders a thinking turn collapsed by default, expanding in place to its own body (review:F7)', async () => {
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
      {
        index: 1,
        kind: 'thinking',
        timestamp: '2026-07-16T11:00:05+00:00',
        text: 'reconsidering, differently',
        tool: null,
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      },
    ]);

    const detailsList = el.querySelectorAll('.thinking');
    expect(detailsList).toHaveLength(2);
    const [first, second] = Array.from(detailsList) as HTMLDetailsElement[];
    expect(first.open).toBe(false);
    expect(second.open).toBe(false);

    // "Expanding in place" is the component's own claim: opening one turn's
    // `<details>` reveals that turn's own body, distinguishable from a sibling
    // turn's — not just any `<details>` element's native open/close mechanics.
    first.open = true;
    expect(first.querySelector('.th-body')?.textContent).toContain('considering the options');
    expect(second.querySelector('.th-body')?.textContent).toContain('reconsidering, differently');
    expect(first.querySelector('.th-body')?.textContent).not.toContain('reconsidering, differently');
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
    const emitted: SidechainOpenEvent[] = [];
    fixture.componentInstance.openStandalone.subscribe((e) => emitted.push(e));

    const openButton = el.querySelector<HTMLButtonElement>(
      '[data-testid="transcript-sidechain-nested"] [data-testid="transcript-sidechain-open"]',
    );
    expect(openButton).not.toBeNull();
    openButton?.click();

    expect(emitted).toEqual([{ turn, path: [0] }]);
  });

  it("forwards a nested viewer's openStandalone emission through the recursive instance, prefixing its own path (review:F3)", async () => {
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
    const emitted: SidechainOpenEvent[] = [];
    fixture.componentInstance.openStandalone.subscribe((e) => emitted.push(e));

    const innerOpenButton = el.querySelector<HTMLButtonElement>(
      '[data-testid="transcript-sidechain-standalone"] [data-testid="transcript-sidechain-open"]',
    );
    expect(innerOpenButton).not.toBeNull();
    innerOpenButton?.click();

    // The outer turn's own index (0) is prepended in front of the inner emission's own
    // path (also 0) — exactly the collision `review:F3`'s fix disambiguates.
    expect(emitted).toEqual([{ turn: innerSidechainTurn, path: [0, 0] }]);
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
    const emitted: SidechainOpenEvent[] = [];
    fixture.componentInstance.openStandalone.subscribe((e) => emitted.push(e));

    (el.querySelector('[data-testid="transcript-sidechain-open"]') as HTMLButtonElement).click();

    expect(emitted).toEqual([{ turn, path: [3] }]);
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

  describe('turn timestamps render in browser-local time (issue #136, review:F8)', () => {
    // Re-homed from `local-panel/transcript-panel.spec.ts` when `turnClockInfo`/
    // `turnAbsolute` moved here (blizzard#248 D3/D4) — pin both the zone and "now" so the
    // local-day boundary is deterministic.
    beforeEach(() => {
      vi.stubEnv('TZ', 'America/New_York');
      vi.setSystemTime(new Date('2026-07-16T15:00:00.000Z')); // 11:00 EDT
    });

    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllEnvs();
    });

    function envTurn(timestamp: string | null): TranscriptTurn {
      return {
        index: 0,
        kind: 'env',
        timestamp,
        text: 'NODE ENVELOPE',
        tool: null,
        thinking_redacted: false,
        sidechain: null,
        truncated: false,
      };
    }

    it('renders today\'s turn as the local time alone, no day cell, tooltipped with the full date (issue #175)', async () => {
      const { el } = await render([envTurn('2026-07-16T11:00:00+00:00')]); // 07:00 EDT, same local day as "now"

      const turn = el.querySelector('[data-testid="transcript-turn"]');
      expect(turn?.querySelector('.t .day')).toBeNull();
      expect(turn?.querySelector('.t .time')?.textContent).toBe('07:00:00');
      expect(turn?.textContent).not.toContain('UTC');
      expect(turn?.querySelector('.t')?.getAttribute('title')).toBe('2026/07/16 07:00:00');
    });

    it("renders yesterday's turn as \"Yesterday\" above the local time", async () => {
      const { el } = await render([envTurn('2026-07-15T23:30:00+00:00')]); // 19:30 EDT the day before "now"

      const turn = el.querySelector('[data-testid="transcript-turn"]');
      expect(turn?.querySelector('.t .day')?.textContent).toBe('Yesterday');
      expect(turn?.querySelector('.t .time')?.textContent).toBe('19:30:00');
    });

    it('renders an older turn as its yyyy-mm-dd date above the local time', async () => {
      const { el } = await render([envTurn('2026-07-01T11:00:00+00:00')]); // well before "now"

      const turn = el.querySelector('[data-testid="transcript-turn"]');
      expect(turn?.querySelector('.t .day')?.textContent).toBe('2026-07-01');
      expect(turn?.querySelector('.t .time')?.textContent).toBe('07:00:00');
    });

    it('falls back to a bare dash with no tooltip for an absent or unparsable timestamp', async () => {
      const { el } = await render([envTurn(null)]);

      const turn = el.querySelector('[data-testid="transcript-turn"]');
      expect(turn?.querySelector('.t .time')?.textContent).toBe('—');
      expect(turn?.querySelector('.t .day')).toBeNull();
      expect(turn?.querySelector('.t')?.getAttribute('title')).toBeNull();
    });
  });
});
