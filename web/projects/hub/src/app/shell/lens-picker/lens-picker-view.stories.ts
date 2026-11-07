import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import type { LensChoice } from '../nav/project-lens.model';
import { LensPickerView } from './lens-picker-view';

const ALL: LensChoice = { slug: null, label: 'All', color: null };

const HANDFUL: readonly LensChoice[] = [
  ALL,
  { slug: 'blizzard', label: 'blizzard', color: '#5b8def' },
  { slug: 'winter', label: 'winter', color: '#4cc9a4' },
  { slug: 'infra', label: 'infra', color: '#e0a03c' },
];

const MANY: readonly LensChoice[] = [
  ALL,
  ...Array.from({ length: 24 }, (_, i) => ({ slug: `project-${i + 1}`, label: `project-${i + 1}`, color: '#5b8def' })),
];

const meta: Meta<LensPickerView> = {
  title: 'Hub shell/Lens picker',
  component: LensPickerView,
  args: { state: 'ready', choices: HANDFUL, current: null, backLink: '/board', choose: fn() },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export default meta;

type Story = StoryObj<LensPickerView>;

export const Loading: Story = { args: { state: 'loading', choices: [ALL] } };
export const Failed: Story = { args: { state: 'error', choices: [ALL] } };
export const Empty: Story = { args: { state: 'empty', choices: [ALL] } };
export const ReadyOnAll: Story = {};
export const ReadyOnAProject: Story = { args: { current: 'winter' } };
export const ManyProjects: Story = { args: { choices: MANY, current: 'project-3' } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
