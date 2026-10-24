import type { Meta, StoryObj } from '@storybook/angular-vite';

import type { BounceEnvelope } from '../../api/hub';
import { ChunkArtifactBounce } from './chunk-artifact-bounce';

const causeAndDetail: BounceEnvelope = {
  cause: 'failure',
  detail: 'PR #7 in acme/one did not land: `land` answered failure',
  unlanded: [],
  step: null,
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

// An envelope stored before hub steps stopped running commands: a failed step, and no action.
const failedStep: BounceEnvelope = {
  ...causeAndDetail,
  detail: 'PR #7 in acme/one did not land: `tests` exited 2',
  step: { name: 'tests', exit_code: 2, output_tail: '2 failed, 40 passed', log_artifact: 'hub-log.tests' },
};
export const FailedStep: Story = { args: { envelope: failedStep, raw: JSON.stringify(failedStep) } };

const unlanded: BounceEnvelope = {
  ...answeredAction,
  unlanded: [
    { repo: 'acme/one', branch: 'feat/x', commit: 'a1b2c3d', pr: { number: 7, url: 'https://forge.example/acme/one/pull/7' } },
    { repo: 'acme/two', branch: 'feat/x', commit: 'e4f5a6b', pr: null },
  ],
};
export const UnlandedRepos: Story = { args: { envelope: unlanded, raw: JSON.stringify(unlanded) } };
