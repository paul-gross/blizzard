"""GitHub's browser address binding, independent of credentialed API calls."""

from dataclasses import dataclass

from blizzard.hub.domain.config.repositories import RepositoryFields


@dataclass(frozen=True)
class GitHubWebLinks:
    web_origin: str

    def branch_url(self, repository: RepositoryFields, branch_name: str) -> str:
        return f"{self.web_origin}/{repository.owner}/{repository.repo}/tree/{branch_name}"
