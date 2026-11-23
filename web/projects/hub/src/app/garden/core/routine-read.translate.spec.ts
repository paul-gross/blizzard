import {
  gardenMeasurementReadingView,
  gardenProposalCountsRowView,
  gardenProposalCountsView,
  gardenScopeSweepView,
  gardenSweepsView,
  gardenTrendPeriodView,
  gardenTrendView,
} from 'fleet/testing';

import { fromGardenProposalCountsView, fromGardenSweepsView, fromTrendView } from './routine-read.translate';

describe('fromTrendView', () => {
  it('carries each period’s created, outflow, withdrawn, and reopened counts', () => {
    const trend = fromTrendView(
      gardenTrendView({ periods: [gardenTrendPeriodView({ created: 3, outflow: 2, withdrawn: 1, reopened: 4 })] }),
    );
    expect(trend).toEqual({ periods: [{ created: 3, outflow: 2, withdrawn: 1, reopened: 4 }] });
  });

  it('reads a window with no periods as none', () => {
    expect(fromTrendView(gardenTrendView())).toEqual({ periods: [] });
  });
});

describe('fromGardenSweepsView', () => {
  it('carries each scope’s last sweep and each measurement reading', () => {
    const sweeps = fromGardenSweepsView(
      gardenSweepsView({
        last_swept: [
          gardenScopeSweepView({ scope_slug: 'web', finding_set_id: 'fins_1', produced_at: '2026-01-02T00:00:00Z', revisions: { blizzard: 'abc' } }),
          gardenScopeSweepView(),
        ],
        measurements: [gardenMeasurementReadingView({ measurement: '3 open' })],
      }),
    );
    expect(sweeps).toEqual({
      lastSwept: [
        { scopeSlug: 'web', findingSetId: 'fins_1', producedAt: '2026-01-02T00:00:00Z', revisions: { blizzard: 'abc' } },
        { scopeSlug: 'blizzard', findingSetId: null, producedAt: null, revisions: {} },
      ],
      measurements: [{ scopeSlug: 'blizzard', producedAt: '2026-01-10T00:00:00Z', measurement: '3 open' }],
    });
  });

  it('reads nothing swept or measured as empty', () => {
    expect(fromGardenSweepsView(gardenSweepsView())).toEqual({ lastSwept: [], measurements: [] });
  });
});

describe('fromGardenProposalCountsView', () => {
  it('carries each row’s origin, class, and counts', () => {
    const counts = fromGardenProposalCountsView(
      gardenProposalCountsView({
        rows: [gardenProposalCountsRowView({ origin: 'operator', created: 5, open: 1, passed: 2, accepted_with_item: 1, accepted_without_item: 1 })],
      }),
    );
    expect(counts.rows).toEqual([
      {
        origin: 'operator',
        proposalClass: 'stale-docstring',
        created: 5,
        open: 1,
        passed: 2,
        acceptedWithItem: 1,
        acceptedWithoutItem: 1,
      },
    ]);
  });

  it('holds an origin this build does not know as unlisted, and no rows as none', () => {
    const counts = fromGardenProposalCountsView(
      gardenProposalCountsView({ rows: [gardenProposalCountsRowView({ origin: 'imported' as never })] }),
    );
    expect(counts.rows[0].origin).toEqual({ unlisted: 'imported' });
    expect(fromGardenProposalCountsView(gardenProposalCountsView()).rows).toEqual([]);
  });
});
