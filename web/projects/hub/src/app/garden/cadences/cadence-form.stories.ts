import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { CadenceFormStoryHost } from './cadence-form.story-host';

const meta: Meta = {
  title: 'Hub garden/Cadence form',
  decorators: [moduleMetadata({ imports: [CadenceFormStoryHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: (args) => ({ props: args, template: `<app-story-cadence-form ${attrs} />` }),
});

const EVENING = { slug: 'evening', name: 'Evening sweep', cron: '0 20 * * *', zone: 'America/Chicago', spacing: '10d', anchor: 'finished' };

export const Create: Story = host('');
export const CreatePhone: Story = { ...Create, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Edit: Story = {
  ...host('[creating]="false" [draft]="draft"'),
  args: { draft: EVENING },
};
export const RuleErrors: Story = {
  ...host('[draft]="draft" [touched]="true"'),
  args: { draft: { slug: '', cron: '0 20 * *', spacing: '10', anchor: 'finished' } },
};
export const RuleErrorsPhone: Story = { ...RuleErrors, globals: { viewport: { value: 'phone', isRotated: false } } };
export const SlugTaken: Story = {
  ...host('[draft]="draft" slugError="the project already holds slug evening"'),
  args: { draft: { slug: 'evening', spacing: '1d' } },
};
export const Conflict: Story = {
  ...host('[creating]="false" [draft]="draft" formError="the cadence changed since it was read"'),
  args: { draft: { ...EVENING, cron: '0 21 * * *' } },
};
