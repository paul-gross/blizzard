import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitTabContent } from './kit-tab-content';
import { KitTabGroup } from './kit-tab-group';
import { KitTabPanel } from './kit-tab-panel';
import { KitTabs, type KitTabOption } from './kit-tabs';

const OPTIONS: readonly KitTabOption[] = [
  { value: 'general', label: 'General' },
  { value: 'artifacts', label: 'Artifacts' },
  { value: 'transcripts', label: 'Transcripts' },
];

const COUNTED: readonly KitTabOption[] = [
  { value: 'general', label: 'General' },
  { value: 'artifacts', label: 'Artifacts', count: 12 },
  { value: 'transcripts', label: 'Transcripts', count: 0 },
];

const MANY: readonly KitTabOption[] = Array.from({ length: 14 }, (_, i) => ({
  value: `tab-${i}`,
  label: `Node-step ${i + 1}`,
}));

const meta: Meta<KitTabs> = {
  title: 'Kit/Tabs',
  component: KitTabs,
  decorators: [moduleMetadata({ imports: [KitTabGroup, KitTabPanel, KitTabContent] })],
  args: {
    options: OPTIONS,
    active: 'general',
    variant: 'top',
    activeChange: fn(),
  },
  argTypes: { variant: { control: 'inline-radio', options: ['top', 'sub'] } },
  render: (args) => ({
    props: args,
    template: `
      <section fleetKitTabGroup>
        <fleet-kit-tabs [options]="options" [(active)]="active" [variant]="variant" (activeChange)="activeChange($event)" />
        @for (option of options; track option.value) {
          <div [fleetKitTabPanel]="option.value">
            <ng-template fleetKitTabContent><p style="padding: 8px 12px; color: var(--text)">{{ option.label }} panel</p></ng-template>
          </div>
        }
      </section>`,
  }),
};
export default meta;

type Story = StoryObj<KitTabs>;

export const Top: Story = {};
export const Sub: Story = { args: { variant: 'sub' } };
export const WithCounts: Story = { args: { options: COUNTED } };
export const NoActiveTab: Story = { args: { active: null } };
export const Overflowing: Story = {
  args: { options: MANY, active: 'tab-0' },
};
export const NarrowPhone: Story = {
  args: { options: COUNTED },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  args: { options: COUNTED },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
