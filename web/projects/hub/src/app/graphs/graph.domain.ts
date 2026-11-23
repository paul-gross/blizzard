// bzh:frontend-domain-boundary — a minted graph as the hub reads it; no wire, no Angular.

/** A vocabulary value this build does not list, kept in its original spelling. */
export interface UnknownGraphValue {
  readonly kind: 'unknown';
  readonly raw: string;
}

/** What one choice's `to:` names: a node in this graph, the reserved `done` terminal, or another graph. */
export type ChoiceTargetKind = 'node' | 'done' | 'graph' | UnknownGraphValue;

/** Where a node's step runs. */
export type Executor = 'runner' | 'hub' | UnknownGraphValue;

/** Who issues a node's exit judgement. */
export type JudgedBy = 'worker' | 'human' | UnknownGraphValue;

/** A graph version's standing within its name's lineage. */
export type GraphLifecycle = 'effective' | 'superseded' | 'retired' | UnknownGraphValue;

/** A graph vocabulary value as text — a known value as itself, an unknown one raw. */
export function graphVocabularyText(value: string | UnknownGraphValue): string {
  return typeof value === 'string' ? value : value.raw;
}

/** One minted graph version, as the graph list summarizes it. */
export interface GraphSummary {
  readonly graphId: string;
  readonly name: string;
  readonly createdAt: string;
  /** Whether this version is its name's effective one, as the hub judges it. */
  readonly effective: boolean;
  readonly retired: boolean;
  readonly lifecycle: GraphLifecycle;
}

/** One choice a node offers. */
export interface GraphChoice {
  readonly choiceId: string;
  readonly name: string;
  readonly description: string;
}

/** One node of a graph. */
export interface GraphNode {
  readonly nodeId: string;
  readonly name: string;
  readonly executor: Executor;
  /** The session mode, as authored. */
  readonly session: string;
  /** The session a `resume:`/`fresh:` mode names, or `null` for a bare mode. */
  readonly sessionSource: string | null;
  readonly judgedBy: JudgedBy;
  readonly judgementPrompt: string | null;
  readonly action: string | null;
  readonly actionArgs: Readonly<Record<string, readonly string[]>>;
  readonly prompt: string | null;
  readonly checks: readonly string[];
  /** The names of the artifacts the node produces. */
  readonly produces: readonly string[];
  readonly retriesMax: number | null;
  readonly retriesExhausted: string | null;
  readonly choices: readonly GraphChoice[];
}

/** One edge of a graph: a node's choice and where it leads. */
export interface GraphEdge {
  readonly choiceId: string;
  readonly fromNodeId: string;
  readonly toNodeName: string;
  readonly targetKind: ChoiceTargetKind;
  readonly targetGraph: string | null;
  readonly promptAddendum: string | null;
}

/** A session declaration's rotation bounds. */
export interface GraphRotatePolicy {
  readonly maxContextTokens: number | null;
  readonly maxInvocations: number | null;
  readonly maxTranscriptBytes: number | null;
}

/** One graph-level session declaration. */
export interface GraphSession {
  readonly name: string;
  readonly model: readonly string[];
  readonly harnesses: readonly string[];
  readonly effort: string | null;
  readonly compactionWindow: string | null;
  readonly rotate: GraphRotatePolicy | null;
}

/** One minted graph's full immutable structure. */
export interface Graph {
  readonly graphId: string;
  readonly name: string;
  readonly entryNodeId: string;
  readonly retired: boolean;
  readonly nodes: readonly GraphNode[];
  readonly edges: readonly GraphEdge[];
  readonly sessions: readonly GraphSession[];
}
