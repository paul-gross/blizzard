import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import {
  assetArtifactView,
  commitArtifactView,
  deltaArtifactView,
  fixtureInstant,
  surveyArtifactView,
} from 'fleet/testing';
import { ChunkArtifactsPanel } from './chunk-artifacts-panel';

const few = [commitArtifactView(), assetArtifactView(), surveyArtifactView(), deltaArtifactView()];
const forty = Array.from({ length: 40 }, (_, i) =>
  assetArtifactView({
    key: `artifact-${String(i + 1).padStart(2, '0')}`,
    name: `artifact-${i + 1}`,
    epoch: Math.floor(i / 8) + 1,
    recorded_at: fixtureInstant(-3600 + i * 60),
    content: `Content of artifact ${i + 1}.`,
  }),
);

const meta: Meta<ChunkArtifactsPanel> = {
  title: 'Chunk/Artifacts panel',
  component: ChunkArtifactsPanel,
  args: {
    artifacts: few,
    selectedKey: null,
    drilldown: false,
    testidPrefix: 'artifacts-panel',
    pickArtifact: fn(),
    clearArtifact: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<div style="height: 420px"><fleet-chunk-artifacts-panel [artifacts]="artifacts" [selectedKey]="selectedKey" [drilldown]="drilldown" [testidPrefix]="testidPrefix" (pickArtifact)="pickArtifact($event)" (clearArtifact)="clearArtifact()" /></div>`,
  }),
};
export default meta;

type Story = StoryObj<ChunkArtifactsPanel>;

export const Empty: Story = { args: { artifacts: [] } };
export const WithSelection: Story = { args: { selectedKey: 'survey' } };
export const FortyArtifacts: Story = { args: { artifacts: forty } };
export const Drilldown: Story = {
  globals: { viewport: { value: 'phone', isRotated: false } },
  args: { selectedKey: 'survey', drilldown: true },
};
