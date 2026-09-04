import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { GraphNodeView, GraphView } from '../api/hub';
import { KitFactList, type KitFact } from '../kit/kit-fact-list';
import { KitPanel } from '../kit/kit-panel';
import { KitProseBlock } from '../kit/kit-prose-block';
import {
  type DiagramSelection,
  type ResolvedChoiceSelection,
  resolveSelectedChoice,
  resolveSelectedNode,
} from './graph-diagram-selection';
import { type IncomingAddendum, incomingAddenda } from './graph-incoming-addenda';
import type { EdgeTarget } from './graph-layout';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The diagram's detail pane — the selected node's full `GraphNodeView` (including
 * `prompt`/`judgement_prompt` and every inbound edge's `prompt_addendum`,
 * `graph-incoming-addenda.ts`'s own resolver), the selected edge's choice
 * (including its `prompt_addendum`, issue #208), or a neutral hint when nothing is
 * selected. Presentational only: `graph`/`selection` are plain inputs, resolved
 * against `graph-diagram-selection.ts`'s pure resolvers
 * (`bzh:frontend-container-presentational`). Field rendering (retries, produces,
 * session) goes through `graph-node.ts`'s shared helpers — the same ones
 * `graph-node-table.ts` uses — so the pane and the structured table can't drift on
 * how a field displays. Chrome comes from `fleet/lib/kit/` (`bzh:frontend-kit-floor`):
 * the fact grid is `fleet-kit-fact-list`, and every block of agent-facing prose —
 * a node's prompt, its inbound addenda, a choice's description, an edge's prompt
 * addendum — is `fleet-kit-prose-block`. A choice's description earns that
 * treatment like the rest: the judgement prompt the agent reads lists each choice
 * by name and description (`src/blizzard/runner/loop/judgement_prompt.py`), so it
 * is prose a person wrote for an agent, the same `context` side as a node's prompt.
 */
@Component({
  selector: 'fleet-graph-diagram-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel, KitFactList, KitProseBlock],
  templateUrl: './graph-diagram-detail.html',
  styleUrl: './graph-diagram-detail.css',
})
export class GraphDiagramDetail {
  readonly graph = input.required<GraphView>();
  readonly selection = input<DiagramSelection | null>(null);

  protected readonly selectedNode = computed<GraphNodeView | null>(() => resolveSelectedNode(this.graph(), this.selection()));
  protected readonly selectedChoice = computed(() => resolveSelectedChoice(this.graph(), this.selection()));

  /** Every inbound edge's `prompt_addendum` for the selected node — empty when
   * nothing is selected, the selection is an edge, or the node has no inbound
   * addenda at all. */
  protected readonly incomingAddenda = computed<readonly IncomingAddendum[]>(() => {
    const node = this.selectedNode();
    return node ? incomingAddenda(this.graph(), node) : [];
  });

  protected nodeName(nodeId: string): string {
    return (this.graph().nodes ?? []).find((n) => n.node_id === nodeId)?.name ?? nodeId;
  }

  /** The selected edge's target, rendered for the "Target" row: a node's name, the
   * reserved `done` terminal, or the target graph's name for a migration edge. */
  protected targetLabel(target: EdgeTarget): string {
    switch (target.kind) {
      case 'node':
        return this.nodeName(target.nodeId);
      case 'done':
        return 'done';
      case 'graph':
        return target.targetGraph;
    }
  }

  /** The selected node's record as an aligned fact grid (`fleet-kit-fact-list`) —
   * every row is plain text, so this is a straight `KitFactText` list rather than
   * the templated form `finding-panel.ts`'s own `factRows` needs. */
  protected nodeFactRows(node: GraphNodeView): readonly KitFact[] {
    return [
      { label: 'Node', value: node.name },
      { label: 'Executor', value: node.executor },
      { label: 'Session', value: sessionLabel(node) },
      { label: 'Judged by', value: node.judged_by },
      { label: 'Mode', value: node.mode ?? '—' },
      { label: 'Retries', value: retriesLabel(node) },
      { label: 'Checks', value: listOrDash(node.checks) },
      { label: 'Produces', value: listOrDash(producesNames(node)) },
    ];
  }

  /** The selected edge's choice as an aligned fact grid — `Target`'s own `testid`
   * carries forward the one the hand-rolled `<dd>` used to carry directly. */
  protected edgeFactRows(choice: ResolvedChoiceSelection): readonly KitFact[] {
    return [
      { label: 'Choice', value: choice.name },
      { label: 'Source', value: this.nodeName(choice.fromNodeId) },
      { label: 'Target', value: this.targetLabel(choice.target), testid: 'graph-diagram-detail-target' },
      { label: 'Kind', value: choice.edgeKind },
    ];
  }
}
