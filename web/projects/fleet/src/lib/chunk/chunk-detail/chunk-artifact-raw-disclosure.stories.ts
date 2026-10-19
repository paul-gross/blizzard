import type { Meta, StoryObj } from '@storybook/angular-vite';

import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';

const meta: Meta<ChunkArtifactRawDisclosure> = {
  title: 'Chunk/Artifact raw disclosure',
  component: ChunkArtifactRawDisclosure,
  args: { raw: '{"cause":"missing_artifact","detail":"The step ended without declaring `commit`."}', testid: 'artifact' },
  render: (args) => ({
    props: args,
    template: `<fleet-chunk-artifact-raw-disclosure [raw]="raw" [testid]="testid"><p>The rendered form of the content projects here.</p></fleet-chunk-artifact-raw-disclosure>`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactRawDisclosure>;

// The open state is reached by clicking the toggle: `showRaw` is internal state, not an input.
export const Closed: Story = {};
