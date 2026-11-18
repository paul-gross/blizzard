import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import type { UrgencyView } from 'fleet';

import { ChoreFormStoryHost } from './chore-form.story-host';

const meta: Meta = {
  title: 'Hub garden/Chore form',
  decorators: [moduleMetadata({ imports: [ChoreFormStoryHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: (args) => ({ props: args, template: `<app-story-chore-form ${attrs} />` }),
});

const NIGHTLY = { key: 'nightly', routine: 'nightly', scopes: 'web, hub', mode: 'delta', promote: false, note: 'docs only' };
const URGENT = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView;

export const Add: Story = host('');
export const AddPhone: Story = { ...Add, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Edit: Story = { ...host('[adding]="false" [draft]="draft"'), args: { draft: NIGHTLY } };
export const UnknownMode: Story = {
  ...host('[adding]="false" [draft]="draft" heldMode="sampled"'),
  args: { draft: { ...NIGHTLY, mode: 'sampled' } },
};
export const ExplicitUrgency: Story = {
  ...host('[adding]="false" [draft]="draft" [urgencies]="urgencies"'),
  args: { draft: { ...NIGHTLY, urgency: 'urgent' }, urgencies: [URGENT] },
};
export const RuleErrors: Story = {
  ...host('[draft]="draft" [touched]="true"'),
  args: { draft: { key: 'Not A Slug', routine: '', scopes: 'web, Hub!' } },
};
export const RuleErrorsPhone: Story = { ...RuleErrors, globals: { viewport: { value: 'phone', isRotated: false } } };
export const KeyRefused: Story = {
  ...host('[draft]="draft" keyError="the cadence already holds a chore nightly"'),
  args: { draft: { key: 'nightly', routine: 'nightly' } },
};
export const Conflict: Story = {
  ...host('[adding]="false" [draft]="draft" formError="the cadence changed since it was read"'),
  args: { draft: NIGHTLY },
};
