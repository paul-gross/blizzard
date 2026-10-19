import type { Meta, StoryObj } from '@storybook/angular-vite';

import {
  assetArtifactView,
  bounceArtifactView,
  commitArtifactView,
  deltaArtifactView,
  surveyArtifactView,
} from 'fleet/testing';
import { ChunkArtifactBody } from './chunk-artifact-body';

const meta: Meta<ChunkArtifactBody> = {
  title: 'Chunk/Artifact body',
  component: ChunkArtifactBody,
  args: { artifact: commitArtifactView(), body: 'full', testid: 'artifact' },
  argTypes: { body: { control: 'inline-radio', options: ['full', 'summary'] } },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-artifact-body [artifact]="artifact" [body]="body" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactBody>;

const unparseable = assetArtifactView({ key: 'survey', name: 'survey', content: '{"scope": "web", "candidates": [' });

export const CommitFull: Story = {};
export const CommitSummary: Story = { args: { body: 'summary' } };
export const PlainTextFull: Story = { args: { artifact: assetArtifactView() } };
export const PlainTextSummary: Story = { args: { artifact: assetArtifactView(), body: 'summary' } };
export const SurveyFull: Story = { args: { artifact: surveyArtifactView() } };
export const SurveySummary: Story = { args: { artifact: surveyArtifactView(), body: 'summary' } };
export const DeltaFull: Story = { args: { artifact: deltaArtifactView() } };
export const DeltaSummary: Story = { args: { artifact: deltaArtifactView(), body: 'summary' } };
export const BounceFull: Story = { args: { artifact: bounceArtifactView() } };
export const BounceSummary: Story = { args: { artifact: bounceArtifactView(), body: 'summary' } };
export const UnparseableFull: Story = { args: { artifact: unparseable } };
export const UnparseableSummary: Story = { args: { artifact: unparseable, body: 'summary' } };
