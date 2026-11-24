import type { Meta, StoryObj } from '@storybook/angular-vite';

import {
  assetChunkArtifact,
  bounceChunkArtifact,
  commitChunkArtifact,
  deltaChunkArtifact,
  surveyChunkArtifact,
} from 'fleet/testing';
import { ChunkArtifactBody } from './chunk-artifact-body';

const meta: Meta<ChunkArtifactBody> = {
  title: 'Chunk/Artifact body',
  component: ChunkArtifactBody,
  args: { artifact: commitChunkArtifact(), body: 'full', testid: 'artifact' },
  argTypes: { body: { control: 'inline-radio', options: ['full', 'summary'] } },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-artifact-body [artifact]="artifact" [body]="body" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactBody>;

const unparseable = assetChunkArtifact({ key: 'survey', name: 'survey', content: '{"scope": "web", "candidates": [' });

export const CommitFull: Story = {};
export const CommitSummary: Story = { args: { body: 'summary' } };
export const PlainTextFull: Story = { args: { artifact: assetChunkArtifact() } };
export const PlainTextSummary: Story = { args: { artifact: assetChunkArtifact(), body: 'summary' } };
export const SurveyFull: Story = { args: { artifact: surveyChunkArtifact() } };
export const SurveySummary: Story = { args: { artifact: surveyChunkArtifact(), body: 'summary' } };
export const DeltaFull: Story = { args: { artifact: deltaChunkArtifact() } };
export const DeltaSummary: Story = { args: { artifact: deltaChunkArtifact(), body: 'summary' } };
export const BounceFull: Story = { args: { artifact: bounceChunkArtifact() } };
export const BounceSummary: Story = { args: { artifact: bounceChunkArtifact(), body: 'summary' } };
export const UnparseableFull: Story = { args: { artifact: unparseable } };
export const UnparseableSummary: Story = { args: { artifact: unparseable, body: 'summary' } };
