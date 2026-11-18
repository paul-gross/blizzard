import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitField } from './kit-field';
import { FormChromeHost } from './kit-form-chrome.story-host';
import { KitField } from './kit-field';

const meta: Meta = {
  component: KitField,
  title: 'Kit/Field',
  component: KitField,
  decorators: [moduleMetadata({ imports: [FormChromeHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: () => ({ template: `<fleet-story-form-chrome ${attrs} />` }),
});

export const Required: Story = host('slug="" hours="4"');
export const Optional: Story = host('slug="ok" hours="4"');
export const WithHint: Story = host('slug="ok-slug" hours="4"');
export const TouchedWithError: Story = host('touched="true"');
export const TouchedWithErrorPhone: Story = {
  ...TouchedWithError,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const TouchedWithErrorPhoneSm: Story = {
  ...TouchedWithError,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
