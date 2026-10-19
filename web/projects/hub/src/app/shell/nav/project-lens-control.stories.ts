import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { ProjectLensControl } from './project-lens-control';
import type { LensChoice } from './project-lens.model';

const ALL: LensChoice = { slug: null, label: 'All', color: null };

const HANDFUL: readonly LensChoice[] = [
  ALL,
  { slug: 'blizzard', label: 'blizzard', color: '#5b8def' },
  { slug: 'winter', label: 'winter', color: '#4cc9a4' },
  { slug: 'infra', label: 'infra', color: '#e0a03c' },
];

const PALETTE = [
  'amber',
  'blue',
  'cyan',
  'green',
  'indigo',
  'lime',
  'orange',
  'pink',
  'red',
  'slate',
  'teal',
  'violet',
];

const MANY: readonly LensChoice[] = [
  ALL,
  ...Array.from({ length: 24 }, (_, i) => ({
    slug: `project-${i + 1}`,
    label: `project-${i + 1}`,
    color: PALETTE[i % PALETTE.length],
  })),
];

const meta: Meta<ProjectLensControl> = {
  title: 'Hub shell/Project lens',
  component: ProjectLensControl,
  args: { choices: HANDFUL, current: null, choose: fn() },
  globals: { viewport: { value: 'desktop', isRotated: false } },
};
export default meta;

type Story = StoryObj<ProjectLensControl>;

export const AllAlone: Story = { args: { choices: [ALL] } };
export const Handful: Story = {};
export const HandfulOnAProject: Story = { args: { current: 'winter' } };
/** Enough projects that the chips scroll inside the control rather than widen the row. */
export const ChipsScroll: Story = {
  args: { choices: MANY, current: 'project-3' },
};
export const LongName: Story = {
  args: {
    choices: [
      ALL,
      {
        slug: 'long',
        label: 'a project whose name runs on far past any sensible chip width',
        color: '#c75bdb',
      },
      { slug: 'short', label: 'short', color: '#4cc9a4' },
    ],
    current: 'long',
  },
};
