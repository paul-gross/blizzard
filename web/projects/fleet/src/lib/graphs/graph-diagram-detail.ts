import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { GraphNodeView, GraphView } from '../api/hub';
import { KitPanel } from '../kit/kit-panel';
import { type DiagramSelection, resolveSelectedChoice, resolveSelectedNode } from './graph-diagram-selection';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The diagram's detail pane — the selected node's full `GraphNodeView` (including
 * `prompt`/`judgement_prompt`), the selected edge's choice (including its
 * `prompt_addendum`, issue #208), or a neutral hint when nothing is selected.
 * Presentational only: `graph`/`selection` are plain inputs,
 * resolved against `graph-diagram-selection.ts`'s pure resolvers
 * (`bzh:frontend-container-presentational`). Field rendering (retries, produces,
 * session) goes through `graph-node.ts`'s shared helpers — the same ones
 * `graph-node-table.ts` uses — so the pane and the structured table can't drift on
 * how a field displays. Chrome comes from `fleet/lib/kit/` (`bzh:frontend-kit-floor`).
 */
@Component({
  selector: 'fleet-graph-diagram-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel],
  template: `
    <fleet-kit-panel label="Detail" data-testid="graph-diagram-detail">
      @if (selectedNode(); as node) {
        <div class="fields" data-testid="graph-diagram-detail-node">
          <dl class="fact-list">
            <dt>Node</dt>
            <dd>{{ node.name }}</dd>
            <dt>Executor</dt>
            <dd>{{ node.executor }}</dd>
            <dt>Session</dt>
            <dd>{{ sessionLabel(node) }}</dd>
            <dt>Judged by</dt>
            <dd>{{ node.judged_by }}</dd>
            <dt>Mode</dt>
            <dd>{{ node.mode ?? '—' }}</dd>
            <dt>Retries</dt>
            <dd>{{ retriesLabel(node) }}</dd>
            <dt>Checks</dt>
            <dd>{{ listOrDash(node.checks) }}</dd>
            <dt>Produces</dt>
            <dd>{{ listOrDash(producesNames(node)) }}</dd>
          </dl>
          @if (node.prompt) {
            <div class="text-block">
              <span class="section-lbl">Prompt</span>
              <pre class="text-body" data-testid="graph-diagram-detail-prompt">{{ node.prompt }}</pre>
            </div>
          }
          @if (node.judgement_prompt) {
            <div class="text-block">
              <span class="section-lbl">Judgement prompt</span>
              <pre class="text-body" data-testid="graph-diagram-detail-judgement-prompt">{{ node.judgement_prompt }}</pre>
            </div>
          }
        </div>
      } @else if (selectedChoice(); as choice) {
        <div class="fields" data-testid="graph-diagram-detail-edge">
          <dl class="fact-list">
            <dt>Choice</dt>
            <dd>{{ choice.name }}</dd>
            <dt>Source</dt>
            <dd>{{ nodeName(choice.fromNodeId) }}</dd>
            <dt>Target</dt>
            <dd data-testid="graph-diagram-detail-target">{{ choice.toNodeId === null ? 'done' : nodeName(choice.toNodeId) }}</dd>
            <dt>Kind</dt>
            <dd>{{ choice.edgeKind }}</dd>
          </dl>
          @if (choice.description) {
            <p class="choice-description" data-testid="graph-diagram-detail-choice-description">{{ choice.description }}</p>
          }
          @if (choice.promptAddendum) {
            <div class="text-block">
              <span class="section-lbl">Prompt addendum</span>
              <pre class="text-body" data-testid="graph-diagram-detail-prompt-addendum">{{ choice.promptAddendum }}</pre>
            </div>
          }
        </div>
      } @else {
        <p class="hint" data-testid="graph-diagram-detail-empty">Select a node or edge in the diagram to inspect it.</p>
      }
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: block;
      min-height: 0;
      height: 100%;
    }
    .fields {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 8px;
    }
    .fact-list {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 2px 10px;
      margin: 0;
      font-size: var(--fs-xs);
    }
    .fact-list dt {
      color: var(--label-dim);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: var(--fs-label);
    }
    .fact-list dd {
      margin: 0;
      color: var(--text);
    }
    .text-block {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .section-lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--label);
    }
    .text-body {
      margin: 0;
      max-height: 220px;
      overflow-y: auto;
      white-space: pre-wrap;
      font-family: var(--mono);
      font-size: var(--fs-xs);
      color: var(--text);
      border: 1px solid var(--line);
      padding: 6px;
    }
    .choice-description {
      margin: 0;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      white-space: pre-wrap;
    }
    .hint {
      margin: 0;
      padding: 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
  `,
})
export class GraphDiagramDetail {
  readonly graph = input.required<GraphView>();
  readonly selection = input<DiagramSelection | null>(null);

  protected readonly selectedNode = computed<GraphNodeView | null>(() => resolveSelectedNode(this.graph(), this.selection()));
  protected readonly selectedChoice = computed(() => resolveSelectedChoice(this.graph(), this.selection()));

  protected nodeName(nodeId: string): string {
    return (this.graph().nodes ?? []).find((n) => n.node_id === nodeId)?.name ?? nodeId;
  }

  protected readonly retriesLabel = retriesLabel;
  protected readonly listOrDash = listOrDash;
  protected readonly producesNames = producesNames;
  protected readonly sessionLabel = sessionLabel;
}
