import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitBeacon } from './kit-beacon';

const meta: Meta<KitBeacon> = {
  title: 'Kit/Beacon',
  component: KitBeacon,
  args: {
    active: false,
    tone: 'amber',
    blink: 'pulse',
    shape: 'square',
    glow: false,
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-beacon [active]="active" [tone]="tone" [blink]="blink" [shape]="shape" [glow]="glow" />`,
  }),
};
export default meta;

type Story = StoryObj<KitBeacon>;

export const Inactive: Story = {};
export const Amber: Story = { args: { active: true } };
export const Red: Story = { args: { active: true, tone: 'red' } };
export const Green: Story = { args: { active: true, tone: 'green' } };
/** A derived-status `Tone`, colored through `toneColor` — WAIT/HUMAN's amber-hi. */
export const StatusTone: Story = { args: { active: true, tone: 'waiting' } };
export const Step: Story = { args: { active: true, blink: 'step' } };
export const Hold: Story = {
  args: { active: true, tone: 'green', blink: 'hold' },
};
/** The mobile titlebar's live light: round, glowing, throbbing. */
export const ActiveDot: Story = {
  args: { active: true, tone: 'spawning', shape: 'dot', glow: true },
};
export const InactiveDot: Story = { args: { shape: 'dot', glow: true } };
export const DotWithoutGlow: Story = {
  args: { active: true, tone: 'spawning', shape: 'dot' },
};
