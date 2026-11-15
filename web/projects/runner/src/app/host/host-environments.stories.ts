import type { Meta, StoryObj } from '@storybook/angular-vite';

import { HostEnvironmentsView } from './host-environments';

const chunkId = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const meta: Meta<HostEnvironmentsView> = {
  title: 'Runner/Host environments',
  component: HostEnvironmentsView,
  args: {
    state: 'ready',
    inUseLabel: '2 / 3 in use',
    groups: [{
      workspaceId: 'blizzard-workspace', capacity: 3, inUse: 2,
      envs: [
        { environmentId: `env-${chunkId}`, held: true, chunkId, chunkRef: 'C-N3YJ9', project: 'blizzard', projectName: 'Blizzard', color: 'amber', holder: 'r-claude', elsewhere: false },
        { environmentId: 'custom-environment-with-a-long-unbroken-identifier', held: true, chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYNZPRR', chunkRef: 'C-NZPRR', project: 'context', projectName: 'Blizzard Context', color: 'teal', holder: 'r-chatgpt', elsewhere: true },
        { environmentId: 'free-environment', held: false, chunkId: null, chunkRef: null, project: null, projectName: null, color: null, holder: null, elsewhere: false },
      ],
    }],
  },
};
export default meta;
type Story = StoryObj<HostEnvironmentsView>;
export const Loaded: Story = {};
export const Narrow: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
