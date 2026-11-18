import type {
  ChunkCountsView,
  RepositoryLinkView,
  SourceLinkView,
} from 'fleet';
import type {
  ProjectChunkCounts,
  ProjectRepositoryLink,
  ProjectSourceLink,
} from './project-read.domain';

export function fromSourceLinkView(wire: SourceLinkView): ProjectSourceLink {
  return {
    project_id: wire.project_id,
    name: wire.name,
    slug: wire.slug,
    linked: wire.linked,
    built_in: wire.built_in,
    revision: wire.revision,
  };
}

export function fromRepositoryLinkView(
  wire: RepositoryLinkView,
): ProjectRepositoryLink {
  return {
    project_id: wire.project_id,
    repository_id: wire.repository_id,
    name: wire.name,
    slug: wire.slug,
    linked: wire.linked,
    revision: wire.revision,
    position: wire.position,
    base_branch: wire.base_branch,
    effective_base_branch: wire.effective_base_branch,
  };
}

export function fromChunkCountsView(wire: ChunkCountsView): ProjectChunkCounts {
  return {
    delivering: wire.delivering,
    done: wire.done,
    done_since: wire.done_since,
    needs_human: wire.needs_human,
    not_ready: wire.not_ready,
    paused: wire.paused,
    ready: wire.ready,
    running: wire.running,
    stopped: wire.stopped,
    terminal: wire.terminal,
    total: wire.total,
    waiting_on_human: wire.waiting_on_human,
  };
}
