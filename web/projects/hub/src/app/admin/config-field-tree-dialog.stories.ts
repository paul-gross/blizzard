import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { SourceDialogHost, UrgencyDialogHost } from './config-field-tree-dialog.story-host';

const meta: Meta = {
  title: 'Hub admin/Config field-tree dialog',
  decorators: [moduleMetadata({ imports: [SourceDialogHost, UrgencyDialogHost] })],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj;

const create = (attrs = ''): Story => ({ render: () => ({ template: `<app-story-source-dialog ${attrs} />` }) });
const edit = (attrs = ''): Story => ({ render: () => ({ template: `<app-story-urgency-dialog ${attrs} />` }) });

export const Create: Story = create();
export const CreateTracker: Story = create('kind="tracker"');
export const Edit: Story = edit();
export const FieldRefusal: Story = {
  render: () => ({
    props: { outcome: { ok: false, fields: { name: 'That name is taken.' }, form: null } },
    template: '<app-story-source-dialog [outcome]="outcome" />',
  }),
};
export const RootRefusal: Story = {
  render: () => ({
    props: { outcome: { ok: false, fields: {}, form: 'The hub could not save this right now.' } },
    template: '<app-story-urgency-dialog [outcome]="outcome" />',
  }),
};
export const CreatePhone: Story = { ...Create, globals: { viewport: { value: 'phone', isRotated: false } } };
export const EditPhoneSm: Story = { ...Edit, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
