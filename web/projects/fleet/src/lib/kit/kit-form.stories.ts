import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { FormChromeHost } from './kit-form-chrome.story-host';

const meta: Meta = {
  title: 'Kit/Form',
  decorators: [moduleMetadata({ imports: [FormChromeHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: () => ({ template: `<fleet-story-form-chrome ${attrs} />` }),
});

export const Idle: Story = host('slug="ok-slug" hours="4"');
export const Submitting: Story = host(
  'slug="ok-slug" hours="4" submitting="true"',
);
export const WithFormLineError: Story = host(
  'slug="ok-slug" hours="4" formError="The hub could not save this repository right now; try again in a moment."',
);
export const WithFormLineErrorPhone: Story = {
  ...WithFormLineError,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const WithFormLineErrorPhoneSm: Story = {
  ...WithFormLineError,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
