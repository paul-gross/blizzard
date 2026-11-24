import type * as hubApi from '../api/hub';
import type * as runnerApi from '../api/runner';
import { unlisted } from '../core/open-lookup';
import type { TranscriptSidechain, TranscriptTool, TranscriptTurn } from './transcript-turn';

type WireTurn = hubApi.TurnSegmentViewOutput | runnerApi.TurnSegmentView;
type WireTool = hubApi.ToolCallSegmentView | runnerApi.ToolCallSegmentView;
type WireSidechain = hubApi.SidechainSegmentViewOutput | runnerApi.SidechainSegmentView;

function translateKind(kind: WireTurn['kind']): { kind: 'env' | 'asst' | 'tool' | 'thinking' | 'sidechain' } | { kind: 'unknown'; rawKind: string } {
  switch (kind) {
    case 'env':
    case 'asst':
    case 'tool':
    case 'thinking':
    case 'sidechain':
      return { kind };
    default:
      return { kind: 'unknown', rawKind: unlisted(kind) };
  }
}

function translateTool(tool: WireTool): TranscriptTool {
  return {
    name: tool.name,
    tool_use_id: tool.tool_use_id,
    input: tool.input,
    input_shape: tool.input_shape,
    input_unparsed: tool.input_unparsed,
    ...(tool.input_truncated === undefined ? {} : { input_truncated: tool.input_truncated }),
    output: tool.output,
    ...(tool.output_patch === undefined ? {} : { output_patch: tool.output_patch }),
    output_truncated: tool.output_truncated,
  };
}

function translateSidechain(sidechain: WireSidechain): TranscriptSidechain {
  return {
    agent_id: sidechain.agent_id,
    agent_type: sidechain.agent_type,
    link: sidechain.link,
    ...(sidechain.parent_tool_use_id === undefined ? {} : { parent_tool_use_id: sidechain.parent_tool_use_id }),
    turns: sidechain.turns.map(translateTranscriptTurn),
  };
}

export function translateTranscriptTurn(turn: WireTurn): TranscriptTurn {
  return {
    index: turn.index,
    ...translateKind(turn.kind),
    text: turn.text,
    timestamp: turn.timestamp,
    thinking_redacted: turn.thinking_redacted,
    truncated: turn.truncated,
    tool: turn.tool === null ? null : translateTool(turn.tool),
    sidechain: turn.sidechain === null ? null : translateSidechain(turn.sidechain),
  };
}
