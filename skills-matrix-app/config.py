"""Loading and validation of the YAML configuration.

The assessment is defined entirely in YAML so it can be updated without a
code change (delivery-plan design principle 11):

* ``domains.yaml``      - domains and their weights, the scoring
                          dimensions, and the shared 0-5 maturity scale.
* ``capabilities.yaml`` - the individual capabilities people rate.

Each capability is rated on every dimension (Knowledge and Delivery) on
the shared 0-5 scale.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

import yaml

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAINS_PATH = os.path.join(APP_DIR, "domains.yaml")
CAPABILITIES_PATH = os.path.join(APP_DIR, "capabilities.yaml")


class ConfigError(Exception):
    """Raised when the YAML configuration is invalid or inconsistent."""


@dataclass
class Dimension:
    id: str
    name: str
    description: str = ""


@dataclass
class MaturityLevel:
    level: int
    label: str
    description: str = ""


@dataclass
class Domain:
    id: str
    name: str
    weight: float
    description: str = ""


@dataclass
class Capability:
    id: str
    domain: str
    name: str
    description: str = ""
    weight: float = 1.0
    # Optional per-capability scoring guidance overriding the shared scale.
    levels: Dict[int, str] = field(default_factory=dict)


@dataclass
class Config:
    domains: List[Domain]
    capabilities: List[Capability]
    dimensions: List[Dimension]
    maturity_levels: Dict[int, MaturityLevel] = field(default_factory=dict)

    @property
    def domain_by_id(self) -> Dict[str, Domain]:
        return {d.id: d for d in self.domains}

    @property
    def capability_by_id(self) -> Dict[str, Capability]:
        return {c.id: c for c in self.capabilities}

    @property
    def dimension_ids(self) -> List[str]:
        return [d.id for d in self.dimensions]

    def capabilities_for(self, domain_id: str) -> List[Capability]:
        return [c for c in self.capabilities if c.domain == domain_id]

    def level_guidance(self, capability: Capability) -> Dict[int, str]:
        """Scoring guidance for a capability: its overrides on top of the
        shared maturity scale, so the UI always has text for every level."""
        guidance = {
            lvl: ml.label + (f" — {ml.description}" if ml.description else "")
            for lvl, ml in self.maturity_levels.items()
        }
        guidance.update(capability.levels)
        return guidance


def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        raise ConfigError(f"Configuration file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_domains(path: str = DOMAINS_PATH):
    data = _load_yaml(path)

    dimensions = [
        Dimension(
            id=d["id"],
            name=d.get("name", d["id"]),
            description=(d.get("description") or "").strip(),
        )
        for d in data.get("dimensions", [])
    ]

    maturity_levels: Dict[int, MaturityLevel] = {}
    for level, body in (data.get("maturity_levels") or {}).items():
        lvl = int(level)
        maturity_levels[lvl] = MaturityLevel(
            level=lvl,
            label=body.get("label", str(lvl)),
            description=(body.get("description") or "").strip(),
        )

    domains = [
        Domain(
            id=d["id"],
            name=d.get("name", d["id"]),
            weight=float(d.get("weight", 1.0)),
            description=(d.get("description") or "").strip(),
        )
        for d in data.get("domains", [])
    ]
    return domains, dimensions, maturity_levels


def load_capabilities(path: str = CAPABILITIES_PATH) -> List[Capability]:
    data = _load_yaml(path)
    capabilities = []
    for c in data.get("capabilities", []):
        levels = {int(k): str(v).strip() for k, v in (c.get("levels") or {}).items()}
        capabilities.append(
            Capability(
                id=c["id"],
                domain=c["domain"],
                name=c.get("name", c["id"]),
                description=(c.get("description") or "").strip(),
                weight=float(c.get("weight", 1.0)),
                levels=levels,
            )
        )
    return capabilities


def load_config(
    domains_path: str = DOMAINS_PATH,
    capabilities_path: str = CAPABILITIES_PATH,
) -> Config:
    """Load and validate the full configuration.

    Raises ``ConfigError`` on any inconsistency (unknown domain reference,
    duplicate id, empty config) so misconfiguration is surfaced loudly.
    """
    domains, dimensions, maturity_levels = load_domains(domains_path)
    capabilities = load_capabilities(capabilities_path)

    if not domains:
        raise ConfigError("No domains defined in domains.yaml")
    if not dimensions:
        raise ConfigError("No scoring dimensions defined in domains.yaml")
    if not maturity_levels:
        raise ConfigError("No maturity_levels defined in domains.yaml")
    if not capabilities:
        raise ConfigError("No capabilities defined in capabilities.yaml")

    domain_ids = {d.id for d in domains}
    seen: set = set()
    for c in capabilities:
        if c.domain not in domain_ids:
            raise ConfigError(
                f"Capability '{c.id}' references unknown domain '{c.domain}'. "
                f"Valid domains: {sorted(domain_ids)}"
            )
        if c.id in seen:
            raise ConfigError(f"Duplicate capability id '{c.id}' in capabilities.yaml")
        seen.add(c.id)

    return Config(
        domains=domains,
        capabilities=capabilities,
        dimensions=dimensions,
        maturity_levels=maturity_levels,
    )
