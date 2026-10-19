import type { Meta, StoryObj } from '@storybook/angular-vite';

import { workRefView } from '../../testing/builders/chunk';
import { ChunkPageHeader } from './chunk-page-header';

const CHUNK_IDS = [
  'ch_01blockedby000000000000001',
  'ch_01blockedby000000000000002',
  'ch_01blockedby000000000000003',
  'ch_01blockedby000000000000004',
  'ch_01blockedby000000000000005',
  'ch_01blockedby000000000000006',
  'ch_01blockedby000000000000007',
  'ch_01blockedby000000000000008',
];

const meta: Meta<ChunkPageHeader> = {
  title: 'Chunk/Page header',
  component: ChunkPageHeader,
  args: {
    chunkId: 'ch_01fixture0000000000000000000',
    status: 'running',
    tone: 'running',
    pointers: [workRefView()],
    blockedBy: [],
    blocking: [],
    linkBase: ['chunks'],
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
export const LongBlockedBy: Story = { args: { status: 'blocked', tone: 'waiting', blockedBy: CHUNK_IDS } };
export const LongBlocking: Story = { args: { blocking: CHUNK_IDS } };
