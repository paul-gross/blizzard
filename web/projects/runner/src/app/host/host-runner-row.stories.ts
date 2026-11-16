import { applicationConfig, type Meta, type StoryObj } from '@storybook/angular-vite';
import { provideRouter } from '@angular/router';

import type { HostRunnerRow } from './host-runner-row.model';
import { HostRunnerRowView } from './host-runner-row';

const row: HostRunnerRow = {
  key: 'rn_1', runnerId: 'rn_1', name: 'r-claude', ref: 'rn_1', tone: 'green', halted: false,
  summary: 'Claude Code · 4 agents · 1 busy', lastTickAt: null, bindings: [], spends: [], harnesses: [],
  gates: ['build', 'deliver', 'unknown-node'], unenforceableGates: [
    { node: 'deliver', reason: 'hub-executed' }, { node: 'unknown-node', reason: 'unknown-node' },
  ],
  throttle: null, hub: null, problems: [], state: [{ label: 'IDLE', tone: 'idle', title: null }], held: [], heldPending: false,
};
const meta: Meta<HostRunnerRowView> = {
  title: 'Runner/Host runner row', component: HostRunnerRowView,
  decorators: [applicationConfig({ providers: [provideRouter([])] })], args: { row },
};
export default meta;
type Story = StoryObj<HostRunnerRowView>;
export const GateWarnings: Story = {};
export const NoGates: Story = { args: { row: { ...row, gates: [], unenforceableGates: [] } } };
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
