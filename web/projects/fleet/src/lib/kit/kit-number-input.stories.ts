import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitNumberInput } from './kit-number-input';
import { FormChromeHost } from './kit-form-chrome.story-host';

const meta: Meta = {
  component: KitNumberInput,
  title: 'Kit/Number input',
  decorators: [moduleMetadata({ imports: [FormChromeHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: () => ({ template: `<fleet-story-form-chrome ${attrs} />` }),
});

export const WithUnit: Story = host('slug="ok-slug" hours="4"');
export const Disabled: Story = host(
  'slug="ok-slug" hours="12x" useDefault="true"',
);
export const InvalidText: Story = host(
  'slug="ok-slug" hours="12x" touched="true"',
);
export const InvalidTextPhone: Story = {
  ...InvalidText,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const InvalidTextPhoneSm: Story = {
  ...InvalidText,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
