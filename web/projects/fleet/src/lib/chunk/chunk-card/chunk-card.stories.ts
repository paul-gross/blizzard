import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { Tone } from '../../kit';
import { ChunkCard } from './chunk-card';
import type { ChunkCardView } from './chunk-card.model';

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

const meta: Meta<ChunkCard> = {
  title: 'Chunk/Card',
  component: ChunkCard,
  args: { card: HUB_CARD, selected: false, gated: false, canControl: false, showHolder: false, selectChunk: fn(), promote: fn() },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 260px">
        <fleet-chunk-card [card]="card" [selected]="selected" [gated]="gated" [canControl]="canControl" [showHolder]="showHolder"
          (selectChunk)="selectChunk($event)" (promote)="promote($event)" />
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<ChunkCard>;

export const HubFilled: Story = {};
export const Selected: Story = { args: { selected: true } };
export const GatedAndPromotable: Story = { args: { gated: true, canControl: true, card: { ...HUB_CARD, promotable: true } } };
export const RunnerFilledWithWorker: Story = {
  args: { card: RUNNER_CARD, showHolder: true },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 260px">
        <fleet-chunk-card [card]="card" [selected]="selected" [showHolder]="showHolder" (selectChunk)="selectChunk($event)">
          <span chunkCardWorker style="font-size: var(--fs-xs); color: var(--label)">beta · RUNNING · 12s</span>
        </fleet-chunk-card>
      </div>`,
  }),
};
export const EachTone: Story = {
  render: (args) => ({
    props: { ...args, cards: TONES.map((tone) => ({ ...HUB_CARD, chunkId: `ch_${tone}`, tone, status: tone, statusWord: tone })) },
    template: `
      <div style="width: 260px; display: flex; flex-direction: column; gap: 4px">
        @for (each of cards; track each.chunkId) {
          <fleet-chunk-card [card]="each" (selectChunk)="selectChunk($event)" />
        }
      </div>`,
  }),
};
