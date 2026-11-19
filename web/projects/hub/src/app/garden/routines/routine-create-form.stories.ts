import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import type { UrgencyView } from 'fleet';

import { RoutineCreateFormStoryHost } from './routine-create-form.story-host';

const meta: Meta = {
  title: 'Hub garden/Routine create form',
  decorators: [moduleMetadata({ imports: [RoutineCreateFormStoryHost] })],
  args: {
    urgencies: [
      { urgency_id: 'u1', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
      { urgency_id: 'u2', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
    ],
  },
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: (args) => ({ props: args, template: `<app-story-routine-create-form [urgencies]="urgencies" ${attrs} />` }),
});

export const Default: Story = host('');
export const Phone: Story = { ...Default, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Refused: Story = {
  ...host('[draft]="draft" scopeError="the scope slug is malformed" formError="the project already holds slug nightly"'),
  args: { draft: { slug: 'nightly', graph: 'garden', scope: 'Bad Scope!' } },
};
