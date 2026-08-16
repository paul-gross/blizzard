import { describe, expect, it } from 'vitest';

import { mergeLateLinks } from './merge-late-links';
import type { TranscriptSidechain, TranscriptTurn } from './transcript-turn';

function turn(index: number, over: Partial<TranscriptTurn> = {}): TranscriptTurn {
  return {
    index,
    kind: 'asst',
    timestamp: null,
    text: '',
    tool: null,
    thinking_redacted: false,
    sidechain: null,
    truncated: false,
    ...over,
  };
}

function call(index: number, toolUseId: string, over: Partial<TranscriptTurn> = {}): TranscriptTurn {
  return turn(index, {
    kind: 'tool',
    tool: {
      name: 'Task',
      input: {},
      input_unparsed: null,
      input_shape: 'object',
      tool_use_id: toolUseId,
      output: null,
      output_truncated: false,
    },
    ...over,
  });
}

function outputPatch(index: number, toolUseId: string, output: string): TranscriptTurn {
  return turn(index, {
    kind: 'tool',
    tool: {
      name: '',
      input: {},
      input_unparsed: null,
      input_shape: 'absent',
      tool_use_id: toolUseId,
      output,
      output_truncated: false,
      output_patch: true,
    },
  });
}

function sidechain(over: Partial<TranscriptSidechain> = {}): TranscriptSidechain {
  return {
    agent_id: 'agent-1',
    agent_type: 'reviewer',
    link: 'agent-id-late',
    turns: [],
    ...over,
  };
}

function lateSidechain(index: number, over: Partial<TranscriptSidechain> = {}): TranscriptTurn {
  return turn(index, {
    kind: 'sidechain',
    sidechain: sidechain({ parent_tool_use_id: 'toolu_T', ...over }),
  });
}

describe('mergeLateLinks', () => {
  it('folds an output patch onto the call it names and drops the patch turn', () => {
    const merged = mergeLateLinks([call(0, 'toolu_T'), turn(1), outputPatch(2, 'toolu_T', '3 blockers')]);

    expect(merged.map((t) => t.index)).toEqual([0, 1]);
    expect(merged[0].tool?.output).toBe('3 blockers');
  });

  it('nests a late sidechain under its spawning call', () => {
    const merged = mergeLateLinks([call(0, 'toolu_T'), lateSidechain(1, { turns: [turn(0, { text: 'hi' })] })]);

    expect(merged).toHaveLength(1);
    expect(merged[0].sidechain?.agent_id).toBe('agent-1');
    expect(merged[0].sidechain?.turns.map((t) => t.text)).toEqual(['hi']);
  });

  it('coalesces one agent’s fragments across windows into a single conversation, in order', () => {
    const merged = mergeLateLinks([
      call(0, 'toolu_T'),
      lateSidechain(1, { turns: [turn(0, { text: 'first' })] }),
      lateSidechain(2, { turns: [turn(0, { text: 'second' })] }),
    ]);

    expect(merged).toHaveLength(1);
    expect(merged[0].sidechain?.turns.map((t) => t.text)).toEqual(['first', 'second']);
  });

  it('leaves a late turn top-level when its call is in a segment this read does not cover', () => {
    const merged = mergeLateLinks([outputPatch(0, 'toolu_ELSEWHERE', 'out'), lateSidechain(1)]);

    // Passed through rather than dropped: unnestable is not the same as unwanted.
    expect(merged.map((t) => t.index)).toEqual([0, 1]);
  });

  it('keeps a second subagent top-level rather than overwriting the one already nested', () => {
    const held = sidechain({
      agent_id: 'agent-1',
      link: 'agent-id',
      turns: [turn(0, { text: 'held' })],
    });
    const merged = mergeLateLinks([
      call(0, 'toolu_T', { sidechain: held }),
      lateSidechain(1, {
        agent_id: 'agent-2',
        turns: [turn(0, { text: 'other' })],
      }),
    ]);

    expect(merged).toHaveLength(2);
    expect(merged[0].sidechain?.turns.map((t) => t.text)).toEqual(['held']);
  });

  it('does not mutate the turns it was given', () => {
    const anchor = call(0, 'toolu_T');
    mergeLateLinks([anchor, outputPatch(1, 'toolu_T', 'out'), lateSidechain(2)]);

    expect(anchor.tool?.output).toBeNull();
    expect(anchor.sidechain).toBeNull();
  });

  it('passes an ordinary transcript through unchanged', () => {
    const turns = [turn(0, { text: 'a' }), call(1, 'toolu_T'), turn(2, { text: 'b' })];

    expect(mergeLateLinks(turns)).toEqual(turns);
  });
});
