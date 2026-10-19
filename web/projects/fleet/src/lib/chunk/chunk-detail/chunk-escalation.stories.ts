import type { Meta, StoryObj } from '@storybook/angular-vite';

import { chunkDetail, chunkEscalationView, escalatedChunkDetail } from 'fleet/testing';
import { ChunkEscalation } from './chunk-escalation';

const meta: Meta<ChunkEscalation> = {
  title: 'Chunk/Escalation',
  component: ChunkEscalation,
  args: { detail: escalatedChunkDetail() },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-detail-escalation [detail]="detail" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkEscalation>;

export const WrappedAndRawCommands: Story = {};
export const RawCommandAlone: Story = {
  args: {
    detail: escalatedChunkDetail({ escalation: chunkEscalationView({ wrapped_takeover_command: undefined }) }),
  },
};
export const ProseGuidance: Story = {
  args: {
    detail: escalatedChunkDetail({
      escalation: chunkEscalationView({
        cause: 'bounce-cap',
        detail: 'The chunk was bounced back eight times without passing review.',
        takeover_command: '',
        wrapped_takeover_command: undefined,
      }),
    }),
  },
};
export const NothingToTakeOver: Story = { args: { detail: chunkDetail() } };
