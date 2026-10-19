import type { Meta, StoryObj } from '@storybook/angular-vite';

import { findingCandidate, findingSurvey } from 'fleet/testing';
import { ChunkArtifactSurvey } from './chunk-artifact-survey';

const one = findingSurvey();
const many = findingSurvey({
  candidates: [
    findingCandidate(),
    findingCandidate({ class: 'dead-code', locus: 'web/projects/hub/src', summary: 'An exported helper has no importer.' }),
    findingCandidate({ class: 'drift', locus: 'blizzard/hub', summary: 'A doc table disagrees with the registry.', ref: 'blizzard#1201' }),
    findingCandidate({ class: 'coverage', locus: 'web/projects/runner', summary: 'A container has no spec.', introduced: '9f2c4e1a7b3d' }),
  ],
});

const meta: Meta<ChunkArtifactSurvey> = {
  title: 'Chunk/Artifact survey',
  component: ChunkArtifactSurvey,
  args: { survey: one, raw: JSON.stringify(one, null, 2), testid: 'artifact' },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-artifact-survey [survey]="survey" [raw]="raw" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactSurvey>;

export const OneFinding: Story = {};
export const ManyFindings: Story = { args: { survey: many, raw: JSON.stringify(many, null, 2) } };
