import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { KitTabContent } from './kit-tab-content';
import { KitTabGroup } from './kit-tab-group';
import { KitTabPanel } from './kit-tab-panel';
import { KitTabs } from './kit-tabs';

const meta: Meta<KitTabPanel> = {
  title: 'Kit/Tab panel',
  component: KitTabPanel,
  decorators: [moduleMetadata({ imports: [KitTabs, KitTabGroup, KitTabPanel, KitTabContent] })],
  render: () => ({
    props: {
      options: [
        { value: 'one', label: 'One' },
        { value: 'two', label: 'Two' },
      ],
    },
    template: `
      <section fleetKitTabGroup>
        <fleet-kit-tabs [options]="options" active="one" />
        <div fleetKitTabPanel="one"><ng-template fleetKitTabContent><p style="padding: 8px 12px; color: var(--text)">Panel one</p></ng-template></div>
        <div fleetKitTabPanel="two"><ng-template fleetKitTabContent><p style="padding: 8px 12px; color: var(--text)">Panel two</p></ng-template></div>
      </section>`,
  }),
};
export default meta;

type Story = StoryObj<KitTabPanel>;

/** {@link KitTabPanel} in its composition: a group holding the strip and the panels it switches. */
export const Composition: Story = {};
