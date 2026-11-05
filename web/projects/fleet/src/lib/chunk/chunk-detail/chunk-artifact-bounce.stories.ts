import type { Meta, StoryObj } from '@storybook/angular-vite';

import type { BounceEnvelope } from '../../api/hub';
import { ChunkArtifactBounce } from './chunk-artifact-bounce';

const causeAndDetail: BounceEnvelope = {
  cause: 'failure',
  detail: 'PR #7 in acme/one did not land: `land` answered failure',
  unlanded: [],
  action: null,
};

const meta: Meta<ChunkArtifactBounce> = {
  title: 'Chunk/Artifact bounce',
  component: ChunkArtifactBounce,
  args: { envelope: causeAndDetail, raw: JSON.stringify(causeAndDetail), testid: 'artifact' },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-artifact-bounce [envelope]="envelope" [raw]="raw" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactBounce>;

export const CauseAndDetail: Story = {};

const answeredAction: BounceEnvelope = {
  ...causeAndDetail,
  action: { name: 'land', choice: 'failure', log_artifact: 'hub-log.land' },
};
export const AnsweredAction: Story = { args: { envelope: answeredAction, raw: JSON.stringify(answeredAction) } };

const unlanded: BounceEnvelope = {
  ...answeredAction,
  unlanded: [
    { repo: 'acme/one', branch: 'feat/x', commit: 'a1b2c3d', pr: { number: 7, url: 'https://forge.example/acme/one/pull/7' } },
    { repo: 'acme/two', branch: 'feat/x', commit: 'e4f5a6b', pr: null },
  ],
};
export const UnlandedRepos: Story = { args: { envelope: unlanded, raw: JSON.stringify(unlanded) } };
