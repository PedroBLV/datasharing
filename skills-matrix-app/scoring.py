"""Scoring logic for the skills matrix.

Every capability is rated on two dimensions — Knowledge and Delivery — on
the 0-5 maturity scale. Scores roll up by capability weight within a
domain, and by domain weight across domains. Keeping the two dimensions
separate is the point: it distinguishes people who *know* a thing from
people who have *delivered* it.

Headline numbers, per dimension:

* domain score    - capability-weight-weighted mean within a domain.
* overall score   - domain-weight-weighted mean across domains.
* maturity level  - score rounded to the nearest 0-5 band.

Plus the **knowledge-delivery gap** (knowledge minus delivery), which
surfaces capabilities understood in theory but not yet shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from config import Config

# How many domains to surface as strengths / development areas.
TOP_N = 3
# Knowledge running this far ahead of delivery flags "knows it, hasn't
# shipped it".
KNOWLEDGE_DELIVERY_GAP = 2.0
# Delivery at/above this counts as a genuine, shipped strength.
DELIVERY_STRENGTH = 4.0


@dataclass
class CapabilityScore:
    capability_id: str
    capability_name: str
    domain: str
    scores: Dict[str, float] = field(default_factory=dict)  # dim_id -> score

    def gap(self) -> Optional[float]:
        k = self.scores.get("knowledge")
        d = self.scores.get("delivery")
        if k is None or d is None:
            return None
        return round(k - d, 2)


@dataclass
class DomainScore:
    domain_id: str
    domain_name: str
    weight: float
    scores: Dict[str, float] = field(default_factory=dict)  # dim_id -> score
    num_capabilities: int = 0

    def gap(self) -> Optional[float]:
        k = self.scores.get("knowledge")
        d = self.scores.get("delivery")
        if k is None or d is None:
            return None
        return round(k - d, 2)


def maturity_label(score: float, config: Config) -> str:
    level = max(0, min(5, round(score)))
    ml = config.maturity_levels.get(level)
    return ml.label if ml else str(level)


def _weighted_mean(pairs: List[tuple]) -> float:
    total_w = sum(w for _, w in pairs) or 1.0
    return sum(v * w for v, w in pairs) / total_w


def score_person(responses: pd.DataFrame, config: Config) -> dict:
    """Compute capability, domain and overall scores for one person.

    Returns a dict with keys: ``capabilities`` (cap_id -> CapabilityScore),
    ``domains`` (domain_id -> DomainScore), ``overall`` (dim_id -> float)
    and ``overall_combined`` (float).
    """
    capability_by_id = config.capability_by_id
    domain_by_id = config.domain_by_id
    dim_ids = config.dimension_ids

    # capability_id -> dimension -> score
    cap_scores: Dict[str, CapabilityScore] = {}
    for _, row in responses.iterrows():
        cap = capability_by_id.get(row["capability_id"])
        if cap is None:
            continue  # stale response for a removed capability
        cs = cap_scores.setdefault(
            cap.id,
            CapabilityScore(
                capability_id=cap.id,
                capability_name=cap.name,
                domain=cap.domain,
            ),
        )
        cs.scores[row["dimension"]] = float(row["score"])

    # Roll capabilities up into domains, per dimension.
    domain_scores: Dict[str, DomainScore] = {}
    for cap_id, cs in cap_scores.items():
        cap = capability_by_id[cap_id]
        domain = domain_by_id.get(cap.domain)
        if domain is None:
            continue
        ds = domain_scores.setdefault(
            domain.id,
            DomainScore(
                domain_id=domain.id,
                domain_name=domain.name,
                weight=domain.weight,
            ),
        )
        ds.num_capabilities += 1

    # Compute per-domain per-dimension weighted means.
    for domain_id, ds in domain_scores.items():
        for dim in dim_ids:
            pairs = []
            for cap_id, cs in cap_scores.items():
                if capability_by_id[cap_id].domain != domain_id:
                    continue
                if dim in cs.scores:
                    pairs.append((cs.scores[dim], capability_by_id[cap_id].weight))
            if pairs:
                ds.scores[dim] = round(_weighted_mean(pairs), 2)

    # Overall per dimension: domain-weight-weighted across domains.
    overall: Dict[str, float] = {}
    for dim in dim_ids:
        pairs = [
            (ds.scores[dim], ds.weight)
            for ds in domain_scores.values()
            if dim in ds.scores
        ]
        if pairs:
            overall[dim] = round(_weighted_mean(pairs), 2)

    overall_combined = (
        round(sum(overall.values()) / len(overall), 2) if overall else 0.0
    )

    return {
        "capabilities": cap_scores,
        "domains": domain_scores,
        "overall": overall,
        "overall_combined": overall_combined,
    }


def _combined(ds: DomainScore) -> float:
    if not ds.scores:
        return 0.0
    return sum(ds.scores.values()) / len(ds.scores)


def strengths_and_gaps(domain_scores: Dict[str, DomainScore], top_n: int = TOP_N):
    """Return (strengths, development_areas) by combined domain score."""
    ordered = sorted(domain_scores.values(), key=_combined, reverse=True)
    strengths = [d.domain_name for d in ordered[:top_n] if _combined(d) > 0]
    development = [d.domain_name for d in ordered[-top_n:][::-1]]
    return strengths, development


def knowledge_delivery_gaps(
    capability_scores: Dict[str, CapabilityScore],
    threshold: float = KNOWLEDGE_DELIVERY_GAP,
) -> List[dict]:
    """Capabilities understood far better than they've been delivered.

    Returns dicts sorted by largest gap first: the classic "I've read
    about it but never shipped it" signal.
    """
    gaps = []
    for cs in capability_scores.values():
        g = cs.gap()
        if g is not None and g >= threshold:
            gaps.append(
                {
                    "capability": cs.capability_name,
                    "knowledge": cs.scores.get("knowledge"),
                    "delivery": cs.scores.get("delivery"),
                    "gap": g,
                }
            )
    return sorted(gaps, key=lambda x: x["gap"], reverse=True)


def delivery_strengths(
    capability_scores: Dict[str, CapabilityScore],
    threshold: float = DELIVERY_STRENGTH,
) -> List[str]:
    """Capabilities the person has genuinely delivered (high delivery)."""
    return [
        cs.capability_name
        for cs in capability_scores.values()
        if cs.scores.get("delivery", 0) >= threshold
    ]


def build_person_profile(
    person: dict, responses: pd.DataFrame, config: Config
) -> dict:
    """Assemble the full individual profile, including AI-ready summary."""
    scored = score_person(responses, config)
    cap_scores = scored["capabilities"]
    domain_scores = scored["domains"]
    overall = scored["overall"]

    strengths, development = strengths_and_gaps(domain_scores)
    kd_gaps = knowledge_delivery_gaps(cap_scores)
    delivered = delivery_strengths(cap_scores)

    domain_scores_by_name = {
        ds.domain_name: dict(ds.scores) for ds in domain_scores.values()
    }

    summary = _build_summary(
        person, overall, strengths, development, kd_gaps, delivered
    )

    return {
        "person": person.get("name"),
        "role": person.get("role"),
        "team": person.get("team"),
        "location": person.get("location"),
        "overall": overall,
        "overall_combined": scored["overall_combined"],
        "domain_scores": domain_scores,
        "domain_scores_by_name": domain_scores_by_name,
        "capability_scores": cap_scores,
        "strengths": strengths,
        "development_areas": development,
        "delivered_capabilities": delivered,
        "knowledge_delivery_gaps": kd_gaps,
        "next_steps": _suggested_next_steps(domain_scores, kd_gaps),
        "summary": summary,
    }


def _fmt_dim(overall: Dict[str, float]) -> str:
    parts = []
    if "knowledge" in overall:
        parts.append(f"knowledge {overall['knowledge']:.1f}")
    if "delivery" in overall:
        parts.append(f"delivery {overall['delivery']:.1f}")
    return ", ".join(parts)


def _build_summary(
    person: dict,
    overall: Dict[str, float],
    strengths: List[str],
    development: List[str],
    kd_gaps: List[dict],
    delivered: List[str],
) -> str:
    name = person.get("name", "This person")
    role = person.get("role")
    parts = [
        f"{name}{f' ({role})' if role else ''} scores {_fmt_dim(overall)} "
        f"out of 5 overall."
    ]
    if strengths:
        parts.append("Strongest domains: " + ", ".join(strengths) + ".")
    if delivered:
        parts.append("Genuinely delivered: " + ", ".join(delivered[:5]) + ".")
    if development:
        parts.append("Development needed in " + ", ".join(development) + ".")
    if kd_gaps:
        named = ", ".join(
            f"{g['capability']} (knows {g['knowledge']:.0f}/delivers "
            f"{g['delivery']:.0f})"
            for g in kd_gaps[:3]
        )
        parts.append("Knowledge ahead of delivery in " + named + ".")
    return " ".join(parts)


def _suggested_next_steps(
    domain_scores: Dict[str, DomainScore], kd_gaps: List[dict]
) -> List[str]:
    steps: List[str] = []
    # Turn the biggest knowledge-delivery gaps into "go and ship it" steps.
    for g in kd_gaps[:2]:
        steps.append(
            f"Convert knowledge into delivery on {g['capability']} — get "
            f"hands-on and ship it (currently delivery {g['delivery']:.0f})."
        )
    # Lowest combined domains get a build-foundations step.
    lowest = sorted(domain_scores.values(), key=_combined)[:TOP_N]
    for ds in lowest:
        c = _combined(ds)
        if c < 2:
            steps.append(f"Build foundational experience in {ds.domain_name}.")
        elif c < 3:
            steps.append(
                f"Move from guided use to independent delivery in {ds.domain_name}."
            )
        elif c < 4:
            steps.append(f"Take {ds.domain_name} into leading delivery.")
    # De-duplicate while preserving order.
    seen = set()
    deduped = []
    for s in steps:
        if s not in seen:
            deduped.append(s)
            seen.add(s)
    return deduped


# --- Team-level aggregation ----------------------------------------------


def latest_per_person(people: pd.DataFrame) -> pd.DataFrame:
    """One row per person name — the latest by ``date_completed``.

    Team/aggregate views and aggregate exports use this so that a person
    retaking the assessment counts once. The Individual view still has
    access to the full history via ``Database.get_people()``.
    """
    if people.empty:
        return people
    ordered = people.sort_values("date_completed", ascending=False)
    return ordered.drop_duplicates(subset=["name"], keep="first").reset_index(drop=True)


def team_matrix(
    people: pd.DataFrame,
    responses: pd.DataFrame,
    config: Config,
    dimension: str,
) -> pd.DataFrame:
    """person x domain matrix of scores for a single dimension."""
    domain_names = [d.name for d in config.domains]
    rows = []
    for _, person in people.iterrows():
        person_resp = responses[responses["person_id"] == person["person_id"]]
        scored = score_person(person_resp, config)
        row = {
            "person_id": person["person_id"],
            "name": person["name"],
            "role": person["role"],
            "team": person["team"],
            "location": person["location"],
        }
        for ds in scored["domains"].values():
            if dimension in ds.scores:
                row[ds.domain_name] = ds.scores[dimension]
        rows.append(row)
    df = pd.DataFrame(rows)
    # Keep domain columns in config order where present.
    ordered = [c for c in domain_names if c in df.columns]
    meta = [c for c in ["person_id", "name", "role", "team", "location"] if c in df.columns]
    return df[meta + ordered] if not df.empty else df


def team_averages(matrix: pd.DataFrame, config: Config) -> pd.Series:
    """Mean score per domain across the team (for one dimension matrix)."""
    domain_names = [d.name for d in config.domains]
    present = [c for c in domain_names if c in matrix.columns]
    if not present:
        return pd.Series(dtype=float)
    return matrix[present].mean().round(2).sort_values(ascending=False)


def people_strong_in(
    matrix: pd.DataFrame, config: Config, threshold: float = DELIVERY_STRENGTH
) -> Dict[str, List[str]]:
    """Map each domain to people scoring at/above ``threshold`` (one dim)."""
    result: Dict[str, List[str]] = {}
    for d in config.domains:
        if d.name not in matrix.columns:
            continue
        result[d.name] = matrix[matrix[d.name] >= threshold]["name"].tolist()
    return result


def team_gaps(averages: pd.Series, threshold: float = 3.0) -> List[str]:
    """Domains where the team average is below ``threshold``."""
    return [name for name, score in averages.items() if score < threshold]
