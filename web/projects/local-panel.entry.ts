// ng-packagr pins a library's build rootDir to its entry file's own directory (not visible
// in any tsconfig — it overrides whatever rootDir a project sets). local-panel imports fleet
// by source, per the workspace's live-source convention, so an entry file pinned inside
// projects/local-panel/ would put fleet's files outside that rootDir and fail the build.
// Living here instead, one level up, makes rootDir cover both projects. local-panel's real
// entry point (src/public-api.ts) is unchanged; this only re-exports it.
export * from './local-panel/src/public-api';
