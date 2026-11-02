import type { Meta, StoryObj } from '@storybook/angular-vite';

import { chunkDetail, routeView, workRefView } from '../../testing/builders/chunk';
import { chunkControlStub } from '../../testing/chunk-control-stub';
import { ChunkPageHeader } from './chunk-page-header';

const meta: Meta<ChunkPageHeader> = {
  title: 'Chunk/Page header',
  component: ChunkPageHeader,
  args: {
    chunkId: 'ch_01fixture0000000000000000000',
    status: 'running',
    tone: 'running',
    pointers: [workRefView()],
    projectName: null,
    projectColor: null,
    route: null,
    detail: null,
    control: null,
  },
};
export default meta;

type Story = StoryObj<ChunkPageHeader>;

export const NoPointers: Story = { args: { pointers: [] } };
export const ManyPointers: Story = {
  args: {
    pointers: [
      workRefView(),
      workRefView({ ref: '1275', label: 'blizzard#1275', web_url: 'https://github.com/paul-gross/blizzard/issues/1275' }),
      workRefView({ ref: '1270', label: 'blizzard#1270', web_url: 'https://github.com/paul-gross/blizzard/issues/1270' }),
      workRefView({ ref: '88', label: 'blizzard-infra#88', web_url: 'https://github.com/paul-gross/blizzard-infra/issues/88' }),
    ],
  },
};
/** The full identity line — project swatch, work item, the runner holding the route, status — and the ⋮ menu. */
export const ProjectRunnerAndMenu: Story = {
  args: { projectName: 'Blizzard', projectColor: '#4aa3ff', route: routeView(), detail: chunkDetail({ route: routeView() }), control: chunkControlStub() },
};
