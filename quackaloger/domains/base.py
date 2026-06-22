"""Domain plugin protocol and registry.

Undo semantics: one `organize` invocation produces one run journal; domains append
actions to the same RunRecord in execution order (per-run undo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from quackaloger.config import Config
from quackaloger.models import PlanReport


@dataclass
class OrganizeContext:
    cfg: Config
    extract_client: Optional[Any]
    verbose: bool


@dataclass
class OrganizeResult:
    domain_id: str
    report: PlanReport
    books: list[Any]


class OrganizerDomain(Protocol):
    id: str

    def validate_config(self, cfg: Config) -> None:
        """Raise ValueError with a clear message if the domain cannot run."""

    def run(self, ctx: OrganizeContext) -> OrganizeResult:
        ...


DOMAIN_REGISTRY: dict[str, OrganizerDomain] = {}


def register_domain(domain: OrganizerDomain) -> None:
    DOMAIN_REGISTRY[domain.id] = domain


def get_domain(domain_id: str) -> OrganizerDomain:
    if domain_id not in DOMAIN_REGISTRY:
        raise KeyError(f"Unknown domain {domain_id!r}")
    return DOMAIN_REGISTRY[domain_id]
