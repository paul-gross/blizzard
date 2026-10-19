import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitPanel, KitPanelHeader } from './kit-panel';

const meta: Meta<KitPanelHeader> = {
  title: 'Kit/Panel header',
  component: KitPanelHeader,
  render: (args) => ({
    props: args,
    moduleMetadata: { imports: [KitPanel] },
    template: `
      <fleet-kit-panel>
        <div fleetKitPanelHeader style="display: flex; justify-content: space-between">
          <span>Runners</span>
          <span>2 online</span>
        </div>
        <div style="padding: 8px 12px">r-claude — Claude Code, 2 of 4 slots busy</div>
      </fleet-kit-panel>`,
  }),
};
export default meta;

type Story = StoryObj<KitPanelHeader>;

export const OwnsBar: Story = {};
export const SupplementsLabel: Story = {
  render: (args) => ({
    props: args,
    moduleMetadata: { imports: [KitPanel] },
    template: `
      <fleet-kit-panel label="Runners" [count]="2">
        <button fleetKitPanelHeader type="button">Refresh</button>
        <div style="padding: 8px 12px">r-claude — Claude Code, 2 of 4 slots busy</div>
      </fleet-kit-panel>`,
  }),
};
export const LongContent: Story = {
  render: (args) => ({
    props: args,
    moduleMetadata: { imports: [KitPanel] },
    template: `
      <fleet-kit-panel style="width: 240px">
        <div fleetKitPanelHeader style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
          Runners on the xps workstation host, graduated from the feature environment
        </div>
        <div style="padding: 8px 12px">r-claude — Claude Code, 2 of 4 slots busy</div>
      </fleet-kit-panel>`,
  }),
};
