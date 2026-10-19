import type { Meta, StoryObj } from '@storybook/angular-vite';

import { ChunkArtifactBounce } from './chunk-artifact-bounce';
import type { BounceEnvelope } from './parse-bounce-envelope';

const causeAndDetail: BounceEnvelope = {
  cause: 'missing_artifact',
  detail: 'The step ended without declaring `commit`.',
  extras: [],
};

const meta: Meta<ChunkArtifactBounce> = {
  title: 'Chunk/Artifact bounce',
  component: ChunkArtifactBounce,
  args: { envelope: causeAndDetail, raw: JSON.stringify({ cause: causeAndDetail.cause, detail: causeAndDetail.detail }), testid: 'artifact' },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-artifact-bounce [envelope]="envelope" [raw]="raw" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactBounce>;

export const CauseAndDetail: Story = {};
export const ExtraKeys: Story = {
  args: {
    envelope: {
      ...causeAndDetail,
      extras: [
        ['node', 'build'],
        ['attempts', '3'],
        ['missing', '["commit","retrospective"]'],
      ],
    },
    raw: '{"cause":"missing_artifact","detail":"The step ended without declaring `commit`.","node":"build","attempts":3,"missing":["commit","retrospective"]}',
  },
};
export const Neither: Story = {
  args: { envelope: { cause: null, detail: null, extras: [] }, raw: '{}' },
};
