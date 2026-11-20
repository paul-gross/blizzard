import type { Meta, StoryObj } from '@storybook/angular-vite';

import { chunkEscalation } from 'fleet/testing';
import { ChunkEscalation } from './chunk-escalation';

const meta: Meta<ChunkEscalation> = {
  title: 'Chunk/Escalation',
  component: ChunkEscalation,
  args: { detail: { escalation: chunkEscalation() } },
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
    detail: { escalation: chunkEscalation({ wrapped_takeover_command: '' }) },
  },
};
export const ProseGuidance: Story = {
  args: {
    detail: {
      escalation: chunkEscalation({
        cause: 'bounce-cap',
        detail: 'The chunk was bounced back eight times without passing review.',
        takeover_command: '',
        wrapped_takeover_command: '',
      }),
    },
  },
};
export const NothingToTakeOver: Story = { args: { detail: { escalation: null } } };

export const ExhaustedMergeMethods: Story = {
  args: {
    detail: {
      escalation: chunkEscalationView({
        cause: 'merge-method-refused',
        detail: 'repo#42: allowed methods: merge, rebase, squash. ' +
          'merge: the forge rejected the merge commit because the pull request cannot be merged with this method; ' +
          'rebase: the commits cannot be rebased without conflicts; ' +
          'squash: the forge rejected the squash because the resulting commit would be empty.',
        takeover_command: '',
        wrapped_takeover_command: '',
      }),
    },
  },
};
