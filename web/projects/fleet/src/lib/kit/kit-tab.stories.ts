import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitTab, KitTabStrip } from './kit-tab';

const meta: Meta<KitTab> = {
  title: 'Kit/Tab',
  component: KitTab,
  decorators: [moduleMetadata({ imports: [KitTabStrip] })],
  args: { variant: 'top' },
  argTypes: { variant: { control: 'inline-radio', options: ['top', 'sub'] } },
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <button type="button" fleetKitTab [variant]="variant" class="active">General</button>
        <button type="button" fleetKitTab [variant]="variant">Artifacts</button>
      </nav>`,
  }),
};
export default meta;

type Story = StoryObj<KitTab>;

export const ActiveAndInactive: Story = {};
export const Top: Story = { args: { variant: 'top' } };
export const Sub: Story = { args: { variant: 'sub' } };
export const WithCount: Story = {
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <button type="button" fleetKitTab [variant]="variant" class="active">Artifacts <span class="cnt">12</span></button>
        <button type="button" fleetKitTab [variant]="variant">Transcripts <span class="cnt">3</span></button>
      </nav>`,
  }),
};
export const NarrowPhone: Story = {
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
