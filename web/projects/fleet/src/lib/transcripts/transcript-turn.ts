import type * as hubApi from '../api/hub';
import type * as runnerApi from '../api/runner';

/**
 * The turn shape {@link TranscriptViewer} (`./transcript-viewer`) renders: a union of the hub's
 * and the runner's generated turn types, because both planes feed the one viewer
 * (`TRANSCRIPT_SEGMENTS_API`). Nothing hand-declares the shape, so a drift between the two
 * regenerated clients surfaces as a compile error at the consumer.
 */
export type TranscriptTurn = hubApi.TurnSegmentViewOutput | runnerApi.TurnSegmentView;

/** A turn's tool call, derived from the same two generated clients as {@link TranscriptTurn}. */
export type TranscriptTool = hubApi.ToolCallSegmentView | runnerApi.ToolCallSegmentView;

/** A turn's sidechain conversation, derived from the same two generated clients as {@link TranscriptTurn}. */
export type TranscriptSidechain = hubApi.SidechainSegmentViewOutput | runnerApi.SidechainSegmentView;
