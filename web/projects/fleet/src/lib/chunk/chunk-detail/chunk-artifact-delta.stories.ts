import type { Meta, StoryObj } from '@storybook/angular-vite';

import { addFindingOp, findingDelta, goneFindingOp, observedFindingOp } from 'fleet/testing';
import { ChunkArtifactDelta } from './chunk-artifact-delta';

const one = findingDelta();
const many = findingDelta({
  findings: [
    addFindingOp(),
    addFindingOp({ class: 'dead-code', locus: 'web/projects/hub/src', summary: 'An exported helper has no importer.' }),
    observedFindingOp(),
    observedFindingOp({ id: 'fn_01fixture0000000000000000002' }),
    goneFindingOp(),
  ],
});

const meta: Meta<ChunkArtifactDelta> = {
  title: 'Chunk/Artifact delta',
  component: ChunkArtifactDelta,
  args: { delta: one, raw: JSON.stringify(one, null, 2), testid: 'artifact' },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-artifact-delta [delta]="delta" [raw]="raw" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactDelta>;

export const OneFinding: Story = {};
export const ManyFindings: Story = { args: { delta: many, raw: JSON.stringify(many, null, 2) } };
