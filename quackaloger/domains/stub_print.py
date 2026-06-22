"""Backlog domains: registered so the wizard can reference them; no-op organize."""

from quackaloger.config import Config
from quackaloger.domains.base import OrganizeContext, OrganizeResult, register_domain
from quackaloger.models import PlanReport
from quackaloger.ui import ui


class _StubDomain:
    def __init__(self, domain_id: str, label: str):
        self.id = domain_id
        self._label = label

    def validate_config(self, cfg: Config) -> None:
        return

    def run(self, ctx: OrganizeContext) -> OrganizeResult:
        ui.muted(
            f"[{self._label}] Not implemented yet — backlog after Plex. "
            "Remove it from organize_domains in config until shipped."
        )
        rep = PlanReport()
        rep.domain_id = self.id
        return OrganizeResult(domain_id=self.id, report=rep, books=[])


register_domain(_StubDomain("comic_archives", "Comics / manga archives"))
register_domain(_StubDomain("ebooks", "Ebooks"))
