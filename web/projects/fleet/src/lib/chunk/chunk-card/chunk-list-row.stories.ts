import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { Tone } from '../../kit';
import type { ChunkCardView } from './chunk-card.model';
import { ChunkListRow } from './chunk-list-row';

/** A card as the hub fills it: work items, a place on a flow, an open PR, and its cost. */
const HUB_CARD: ChunkCardView = {
  chunkId: 'ch_01M6B9ZW7Z1FEGD6RN36J6BVSV',
  shortId: 'C-BVSV',
  status: 'waiting_on_human',
  tone: 'waiting',
  statusWord: 'waiting_on_human',
  step: 'review',
  stepTitle: 'review (nd_review)',
  workLines: [
    { label: 'hub#712', title: 'One fleet card and list row for both boards' },
    { label: 'hub#689', title: 'Bind every chunk-status colour through toneColor' },
  ],
  itemsRead: true,
  showsDelivery: true,
  places: [{ key: 'flw_1', text: '2nd on blizzard/master', tone: 'spawning', title: 'flw_1' }],
  openPrs: [{ repo: 'blizzard', number: 1796, url: 'https://example.test/pr/1796' }],
  cost: '$4.21',
  projectName: 'Blizzard',
  projectColor: 'cyan',
};

/** A card as the runner fills it: its runner ahead of the step, and its status word. */
const RUNNER_CARD: ChunkCardView = {
  chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  shortId: 'C-3YJ9',
  status: 'WAITING · ASK',
  tone: 'waiting',
  statusWord: 'WAITING · ASK',
  rowStatus: 'WAITING · ASK',
  step: 'build',
  stepTitle: 'build',
  workLines: [{ label: 'blizzard#61', title: 'runner machine panel' }],
  itemsRead: true,
  holder: { host: null, name: 'r-claude', title: 'r-claude' },
  projectName: 'Blizzard',
  projectColor: 'violet',
};

const TONES: readonly Tone[] = ['running', 'needs', 'waiting', 'takeover', 'spawning', 'stale', 'done', 'idle'];

const meta: Meta<ChunkListRow> = {
  title: 'Chunk/List row',
  component: ChunkListRow,
  args: {
    card: { ...HUB_CARD, holder: { host: 'xps', name: 'r-claude', title: 'xps/r-claude · rn_01' } },
    selected: false,
    gated: false,
    selectChunk: fn(),
  },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 720px; --list-columns: minmax(0, 1fr) 220px 90px">
        <fleet-chunk-list-row [card]="card" [selected]="selected" [gated]="gated" (selectChunk)="selectChunk($event)" />
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<ChunkListRow>;

export const HubFilled: Story = {};
export const Selected: Story = { args: { selected: true } };
export const Finished: Story = {
  args: { card: { ...HUB_CARD, tone: 'done', status: 'done', showsDelivery: false, places: [], openPrs: [], completedAt: '2026-10-30T09:30:00Z', finishedWord: 'Completed' } },
};
export const RunnerFilledWithWorker: Story = {
  args: { card: RUNNER_CARD },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 640px; --list-columns: minmax(0, 1fr) 260px 120px">
        <fleet-chunk-list-row [card]="card" [selected]="selected" (selectChunk)="selectChunk($event)">
          <span chunkRowWorker style="color: var(--label)">beta · RUNNING · 12s</span>
        </fleet-chunk-list-row>
      </div>`,
  }),
};
export const EachTone: Story = {
  render: (args) => ({
    props: { ...args, cards: TONES.map((tone) => ({ ...HUB_CARD, chunkId: `ch_${tone}`, tone, status: tone })) },
    template: `
      <div style="width: 720px; --list-columns: minmax(0, 1fr) 220px 90px; display: flex; flex-direction: column; gap: 4px">
        @for (each of cards; track each.chunkId) {
          <fleet-chunk-list-row [card]="each" (selectChunk)="selectChunk($event)" />
        }
      </div>`,
  }),
};
