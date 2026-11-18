import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { FormChromeHost } from './kit-form-chrome.story-host';
import { KitOrDefault } from './kit-or-default';

const meta: Meta = {
  title: 'Kit/Or default',
  component: KitOrDefault,
  decorators: [moduleMetadata({ imports: [FormChromeHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs: string): Story => ({
  render: () => ({ template: `<fleet-story-form-chrome ${attrs} />` }),
});

export const DefaultChosen: Story = host(
  'slug="ok-slug" hours="12x" useDefault="true" touched="true"',
);
export const StatedValue: Story = host('slug="ok-slug" hours="6"');
export const DefaultChosenPhone: Story = {
  ...DefaultChosen,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const DefaultChosenPhoneSm: Story = {
  ...DefaultChosen,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
