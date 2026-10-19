import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitTab, KitTabStrip } from './kit-tab';

const meta: Meta<KitTabStrip> = {
  title: 'Kit/Tab strip',
  component: KitTabStrip,
  decorators: [moduleMetadata({ imports: [KitTab] })],
  args: { variant: 'top' },
  argTypes: { variant: { control: 'inline-radio', options: ['top', 'sub'] } },
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <button type="button" fleetKitTab [variant]="variant" class="active">General</button>
        <button type="button" fleetKitTab [variant]="variant">Artifacts</button>
        <button type="button" fleetKitTab [variant]="variant">Transcripts</button>
      </nav>`,
  }),
};
export default meta;

type Story = StoryObj<KitTabStrip>;

export const Top: Story = {};
export const Sub: Story = { args: { variant: 'sub' } };
export const OverflowingInConstrainedWidth: Story = {
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 280px">
        <nav fleetKitTabStrip [variant]="variant">
          <button type="button" fleetKitTab [variant]="variant" class="active">General</button>
          <button type="button" fleetKitTab [variant]="variant">Artifacts</button>
          <button type="button" fleetKitTab [variant]="variant">Transcripts</button>
          <button type="button" fleetKitTab [variant]="variant">Escalations</button>
          <button type="button" fleetKitTab [variant]="variant">Deliveries</button>
        </nav>
      </div>`,
  }),
};
export const NarrowPhone: Story = {
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
