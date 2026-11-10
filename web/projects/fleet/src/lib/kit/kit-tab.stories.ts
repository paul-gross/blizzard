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
  argTypes: { variant: { control: 'inline-radio', options: ['top', 'sub', 'bottom'] } },
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
        <button type="button" fleetKitTab [variant]="variant" [count]="12" class="active">Artifacts</button>
        <button type="button" fleetKitTab [variant]="variant" [count]="3">Transcripts</button>
      </nav>`,
  }),
};
export const ZeroCount: Story = {
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <button type="button" fleetKitTab [variant]="variant" [count]="0" class="active">Artifacts</button>
        <button type="button" fleetKitTab [variant]="variant" [count]="null">No count</button>
      </nav>`,
  }),
};
export const HotCount: Story = {
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <button type="button" fleetKitTab [variant]="variant" [count]="4" [countHot]="true" class="active">Proposals</button>
        <button type="button" fleetKitTab [variant]="variant" [count]="2" [countHot]="true">Findings</button>
      </nav>`,
  }),
};
export const SubWithCount: Story = {
  args: { variant: 'sub' },
  render: WithCount.render,
};
export const NarrowPhone: Story = {
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const Bottom: Story = {
  args: { variant: 'bottom' },
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <a fleetKitTab [variant]="variant" class="active">Board</a>
        <a fleetKitTab [variant]="variant" [count]="3" countTestid="bottom-count">Asks</a>
        <a fleetKitTab [variant]="variant" role="link" [unavailable]="true">Transcripts</a>
      </nav>`,
  }),
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const BottomPhoneSmall: Story = {
  ...Bottom,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const Unavailable: Story = {
  render: (args) => ({
    props: args,
    template: `
      <nav fleetKitTabStrip [variant]="variant">
        <a fleetKitTab [variant]="variant" class="active">General</a>
        <a fleetKitTab [variant]="variant" role="link" [unavailable]="true">Artifacts</a>
      </nav>`,
  }),
};
