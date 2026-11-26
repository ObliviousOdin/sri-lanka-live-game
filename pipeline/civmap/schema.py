"""Metadata types shared by all domain modules: data sources and metrics."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CATEGORIES = (
    "population", "economy", "income", "labor", "production", "energy", "food", "science",
    "education", "culture", "faith", "society", "hazard", "trade", "infrastructure",
    "landcover", "elevation", "environment", "military",
)


@dataclass
class Source:
    id: str                 # short stable id, e.g. "bea_cagdp2"
    title: str              # dataset title as published
    publisher: str          # agency / organisation
    url: str                # landing page or download URL
    license: str = "public domain / open"
    vintage: str = ""       # data year(s), e.g. "2022"
    countries: list[str] = field(default_factory=list)   # ["lka"], ["usa"] or both
    notes: str = ""


@dataclass
class Metric:
    key: str                # snake_case; identical key in both countries when comparable
    label: str
    unit: str               # "USD", "persons", "%", "km2", "MW", "count", "index", "m" ...
    category: str           # one of CATEGORIES
    kind: str = "extensive"  # "extensive" (additive) or "intensive" (rate / average / share)
    weight: str = "pop"     # allocation weight admin->hex: pop | land | cropland | tree | built | none
    table: str = ""         # file stem in the domain dir: admin_adm2, points_plants, hex ...
    sources: list[str] = field(default_factory=list)
    vintage: str = ""
    description: str = ""
    method: str = ""        # how the value was derived / allocated
    better: str = "neutral"  # higher | lower | neutral (for colour ramps & advisors)
    countries: list[str] = field(default_factory=lambda: ["lka", "usa"])

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.key}: unknown category {self.category}")
        if self.kind not in ("extensive", "intensive"):
            raise ValueError(f"{self.key}: kind must be extensive|intensive")


def write_domain_meta(out_dir: Path, metrics: list[Metric], sources: list[Source], country: str) -> None:
    """Write metrics.json / sources.json for one domain + country."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ss = [asdict(s) for s in sources if not s.countries or country in s.countries]
    ok = {s["id"] for s in ss}
    ms = []
    for m in metrics:
        if country not in m.countries:
            continue
        d = asdict(m)
        d["sources"] = [s for s in m.sources if s in ok]
        ms.append(d)
    (out_dir / "metrics.json").write_text(json.dumps(ms, indent=2))
    (out_dir / "sources.json").write_text(json.dumps(ss, indent=2))
