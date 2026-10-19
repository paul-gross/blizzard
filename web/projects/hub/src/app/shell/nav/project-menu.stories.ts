import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { ProjectMenu } from './project-menu';
import type { LensChoice } from './project-lens.model';

const CHOICES: readonly LensChoice[] = [
  { slug: null, label: 'All', color: null },
  { slug: 'blizzard', label: 'blizzard', color: '#5b8def' },
  { slug: 'winter', label: 'winter', color: '#4cc9a4' },
  {
    slug: 'long',
    label: 'a project whose name runs on far past the width of the menu',
    color: '#c75bdb',
  },
];

const meta: Meta<ProjectMenu> = {
  title: 'Hub shell/Project menu',
  component: ProjectMenu,
  args: { choices: CHOICES, current: 'winter', choose: fn() },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export default meta;

type Story = StoryObj<ProjectMenu>;

export const Phone: Story = {};
export const PhoneSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const AllChosen: Story = { args: { current: null } };
