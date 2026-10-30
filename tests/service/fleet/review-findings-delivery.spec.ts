import { parse } from 'yaml';
import { expect, stepUntil, test } from '../../harness/fixtures';

const SCOPE = 'e2e-review-findings';

type Node = { executor: string; action?: string; prompt?: string; judgement: { prompt?: string } };
type Graph = { name: string; nodes: Record<string, Node> };

function pushAndDeclare(repo: string): string[] {
  return [
    "branch = subprocess.run(['git', '-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "commit = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "subprocess.run(['git', '-C', repo, 'push', 'origin', branch], check=True)",
    `subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', '${repo}', '--branch', branch, '--commit', commit], check=True)`,
  ];
}

function scriptedGraph(body: Graph, repo: string): string {
  const graph = structuredClone(body);
  graph.name = 'default-delivery';
  const nodes = graph.nodes;
  nodes.build!.prompt = [
    'import os, pathlib, subprocess',
    `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${repo}')`,
    "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/scripted-build'], check=True)",
    "p = pathlib.Path(repo, 'BUILD.md')",
    "p.write_text((p.read_text() if p.exists() else '') + 'build pass\\n')",
    "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
    "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a build pass'], check=True)",
    ...pushAndDeclare(repo),
  ].join('\n');
  nodes.build!.judgement.prompt = "verdict('pass', 'checks are green')\n";

  const delta = JSON.stringify({ entries: [
    { ref: 'F1', disposition: 'deferred', severity: 'should-fix', scope: SCOPE, class: 'simplification',
      locus: `${repo}/BUILD.md:1`, summary: 'The changelog line could fold into the existing entry above it.' },
    { ref: 'F2', disposition: 'fixed' },
    { ref: 'F3', disposition: 'refuted' },
  ] });
  nodes.review!.prompt = [
    'import subprocess',
    "subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', 'review-finding-delta'],",
    `    input=${JSON.stringify(delta)}, text=True, check=True)`,
  ].join('\n');
  nodes.review!.judgement.prompt = "verdict('pass', 'cold-eyes review: one should-fix deferred, one fixed, one refuted')\n";

  nodes['pre-push']!.prompt = [
    'import os, pathlib, subprocess',
    `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${repo}')`,
    "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/scripted-build'], check=True)",
    ...pushAndDeclare(repo),
  ].join('\n');
  nodes['pre-push']!.judgement.prompt = "verdict('clean', 'no conflicts; fast-forward ready')\n";
  nodes.retrospective!.prompt = 'pass\n';
  nodes.retrospective!.judgement.prompt = "verdict('recorded', 'landed clean; the review round left one deferred finding')\n";
  expect(nodes.deliver).toEqual(body.nodes.deliver);
  expect(nodes['record-findings']).toEqual(body.nodes['record-findings']);
  return JSON.stringify(graph);
}

test.use({ isolation: 'fresh-tenant' });

test('test_review_finding_delta_mints_exactly_its_deferred_entry_at_landing', async ({ api, forge, issue, repository, runnerHosts }) => {
  test.slow();
  const graphs = await api.get<{ graph_id: string; name: string }[]>('/api/graphs');
  expect(graphs.status).toBe(200);
  const packaged = graphs.body.find((graph) => graph.name === 'bas-dwf');
  expect(packaged).toBeDefined();
  const authoring = await api.get<{ definition_yaml: string }>(`/api/graphs/${packaged!.graph_id}`);
  expect(authoring.status).toBe(200);
  const body = parse(authoring.body.definition_yaml) as Graph;
  expect(body.nodes.deliver).toMatchObject({ executor: 'hub', action: 'land' });
  expect(body.nodes['record-findings']).toMatchObject({ executor: 'hub', action: 'record-findings' });

  const target = await repository();
  const fullName = `${target.owner}/${target.name}`;
  const main = async (): Promise<string> => {
    const response = await forge.call('GET', `/repos/${fullName}/git/ref/heads/main`);
    expect(response.status).toBe(200);
    return (response.body as { object: { sha: string } }).object.sha;
  };
  const before = await main();
  const minted = await api.post('/api/graphs', { data: { definition_yaml: scriptedGraph(body, target.name) } });
  expect(minted.status).toBe(201);
  const host = await runnerHosts.create({ runnerConfig: { workspace: [{
    id: 'workspace-local', root: '${workspace}', provider: 'basic', base_branch: 'main',
    repo: [{ name: target.name, url: target.origin }],
  }] } });
  const { chunk } = await issue('review findings delivery', { repository: target });
  const id = chunk.chunk_id;
  expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
  const detail = await stepUntil(host, async () => (await api.get(`/api/chunks/${id}`)).body,
    (value) => value.status === 'done' || value.status === 'needs_human', 80);
  expect([detail.status, detail.landed]).toEqual(['done', true]);
  expect((detail.history as { choice_name: string; from_node_name: string }[])).toContainEqual(
    expect.objectContaining({ from_node_name: 'record-findings', choice_name: 'recorded' }),
  );

  const findings = await api.get<{ findings: {
    source: string; severity: string; routine_name: string | null; raised_by_chunk_id: string;
    class: string; locus: string;
  }[] }>('/api/findings', { params: { scope: SCOPE, source: 'review' } });
  expect(findings.status).toBe(200);
  expect(findings.body.findings).toEqual([expect.objectContaining({
    source: 'review', severity: 'should-fix', routine_name: null, raised_by_chunk_id: id,
    class: 'simplification', locus: `${target.name}/BUILD.md:1`,
  })]);
  const after = await main();
  expect(after).not.toBe(before);
  const comparison = await forge.call('GET', `/repos/${fullName}/compare/${before}...${after}`);
  expect(comparison.status).toBe(200);
  expect((comparison.body as { files: { filename: string }[] }).files.map((file) => file.filename)).toContain('BUILD.md');
});
