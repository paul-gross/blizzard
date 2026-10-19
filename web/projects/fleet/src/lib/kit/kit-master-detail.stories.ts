import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitMasterDetail } from './kit-master-detail';

const meta: Meta<KitMasterDetail> = {
  title: 'Kit/Master detail',
  component: KitMasterDetail,
  args: {
    paneId: 'node-history',
    listCaption: 'Runs',
    detailCaption: 'Transcript',
    drilldown: false,
    hasSelection: true,
    backLabel: 'Node history',
    testidPrefix: 'master-detail',
    back: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-master-detail [paneId]="paneId" [listCaption]="listCaption" [detailCaption]="detailCaption" [drilldown]="drilldown" [hasSelection]="hasSelection" [backLabel]="backLabel" [testidPrefix]="testidPrefix" (back)="back()" style="height: 320px">
  <button type="button" kit-master-detail-list-action>New</button>
  <ul kit-master-detail-list><li>implement — run 3</li><li>implement — run 2</li><li>plan — run 1</li></ul>
  <div kit-master-detail-detail><p>The selected run's transcript renders here.</p></div>
</fleet-kit-master-detail>`,
  }),
};
export default meta;

type Story = StoryObj<KitMasterDetail>;

export const Default: Story = {};
export const NoCaptions: Story = {
  args: { listCaption: null, detailCaption: null },
};
export const NoSelection: Story = { args: { hasSelection: false } };
export const DrilldownList: Story = {
  args: { drilldown: true, hasSelection: false },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const DrilldownDetail: Story = {
  args: { drilldown: true, hasSelection: true },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const LongCaptions: Story = {
  args: {
    listCaption:
      'Every run of this node across every attempt the runner has made',
    detailCaption:
      'The full transcript of the selected run, including its tool calls',
    backLabel: 'Back to the node history of the chunk awaiting a human answer',
    drilldown: true,
  },
};
export const NarrowPhone: Story = {
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
