import { type FindingFactView, type FindingView } from 'fleet';
import { gardenFindingDetailView, gardenFindingFactView, gardenFindingView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { fromFindingDetailView, fromFindingFactView, fromFindingsPageView, fromFindingView } from './finding.translate';

describe('fromFindingView', () => {
  it('reads every field of a served review-raised finding', () => {
    const finding = fromFindingView(
      gardenFindingView({
        finding_id: 'fnd_9',
        class: 'naming',
        locus: 'src/a.ts:3',
        summary: 'A name',
        note: 'see review',
        state: 'gone',
        exit: 'outflow',
        observed_count: 4,
        introduced: '4ba7ef06d',
        introduced_at: '2026-01-02T00:00:00Z',
        first_observed_at: '2026-01-03T00:00:00Z',
        last_seen_at: '2026-01-04T00:00:00Z',
        routine_name: 'comments',
        scope_slug: 'web',
        source: 'review',
        severity: 'blocking',
        raised_by_chunk_id: 'ch_1',
      }),
    );
    expect(finding).toEqual({
      id: 'fnd_9',
      findingClass: 'naming',
      locus: 'src/a.ts:3',
      summary: 'A name',
      note: 'see review',
      state: 'gone',
      exit: 'outflow',
      observedCount: 4,
      introducedRev: '4ba7ef06d',
      introducedAt: '2026-01-02T00:00:00Z',
      firstObservedAt: '2026-01-03T00:00:00Z',
      lastSeenAt: '2026-01-04T00:00:00Z',
      routineSlug: 'comments',
      scopeSlug: 'web',
      source: 'review',
      severity: 'blocking',
      raisedByChunkId: 'ch_1',
    });
  });

  it('keeps the nullable fields null on an open, routine-raised finding', () => {
    expect(fromFindingView(gardenFindingView())).toMatchObject({
      exit: null,
      severity: null,
      note: null,
      routineSlug: null,
      introducedRev: null,
      introducedAt: null,
      firstObservedAt: null,
      lastSeenAt: null,
      raisedByChunkId: null,
      source: 'routine',
    });
  });

  it('carries a state, exit, source, and severity this build does not know as unlisted', () => {
    const finding = fromFindingView(
      gardenFindingView({
        state: 'quarantined' as FindingView['state'],
        exit: 'expired' as FindingView['exit'],
        source: 'audit' as FindingView['source'],
        severity: 'nit' as FindingView['severity'],
      }),
    );
    expect(finding.state).toEqual({ unlisted: 'quarantined' });
    expect(finding.exit).toEqual({ unlisted: 'expired' });
    expect(finding.source).toEqual({ unlisted: 'audit' });
    expect(finding.severity).toEqual({ unlisted: 'nit' });
  });
});

describe('fromFindingFactView', () => {
  it('reads a known fact', () => {
    expect(
      fromFindingFactView(gardenFindingFactView({ kind: 'superseded', actor: 'u_1', note: 'dup', superseded_by: 'fnd_2', proposal_id: 'gp_1' })),
    ).toEqual({
      kind: 'superseded',
      actor: 'u_1',
      note: 'dup',
      recordedAt: '2026-01-01T00:00:00Z',
      proposalId: 'gp_1',
      supersededBy: 'fnd_2',
    });
  });

  it('carries a fact kind this build does not know as unlisted', () => {
    expect(fromFindingFactView(gardenFindingFactView({ kind: 'merged' as FindingFactView['kind'] })).kind).toEqual({ unlisted: 'merged' });
  });
});

describe('fromFindingDetailView', () => {
  it('keeps the fact chain in the order the hub serves it', () => {
    const detail = fromFindingDetailView(
      gardenFindingDetailView({ facts: [gardenFindingFactView({ kind: 'add' }), gardenFindingFactView({ kind: 'observed' })] }),
    );
    expect(detail.id).toBe('fnd_1');
    expect(detail.facts.map((f) => f.kind)).toEqual(['add', 'observed']);
  });

  it('reads an empty fact chain as empty', () => {
    expect(fromFindingDetailView(gardenFindingDetailView()).facts).toEqual([]);
  });
});

describe('fromFindingsPageView', () => {
  it('reads a page and its cursor', () => {
    const page = fromFindingsPageView({ findings: [gardenFindingView({ finding_id: 'fnd_2' })], next_cursor: 'k' });
    expect(page.findings.map((f) => f.id)).toEqual(['fnd_2']);
    expect(page.nextCursor).toBe('k');
  });

  it('reads an empty last page', () => {
    expect(fromFindingsPageView({ findings: [], next_cursor: null })).toEqual({ findings: [], nextCursor: null });
  });
});
