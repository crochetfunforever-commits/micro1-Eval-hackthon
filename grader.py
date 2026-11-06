#!/usr/bin/env python3
"""Offline grader for the VigiCausality multi-drug ADR causality benchmark.

Reads dataset/tasks.json, validates it with pydantic, flags every model output
that is labelled "Discordant" or that returns an "Unspecified Adverse Event"
term, and atomically writes results/report.json. No network access, no API keys.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = ROOT / "dataset" / "tasks.json"
REPORT_PATH = ROOT / "results" / "report.json"

DISCORDANT_LABEL = "Discordant"
UNSPECIFIED_TERM = "Unspecified Adverse Event"
LAZY_WHO_UMC = {"conditional / unclassified", "conditional/unclassified",
                "unclassified", "conditional"}


NO_CONCOMITANTS = {"", "-", "\u2014", "\u2013", "none", "n/a", "na", "nil"}


class Regimen(BaseModel):
    model_config = ConfigDict(extra="allow")
    suspect_drug: str
    details: str
    concomitant_drugs: str


class ClinicalProfile(BaseModel):
    model_config = ConfigDict(extra="allow")
    regimen: Regimen
    reactions: str
    ground_truth_causality: str
    narrative_summary: str
    lab_data: str


class NaranjoMetrics(BaseModel):
    score: int
    category: str


class AIPredictedOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
    adverse_event_pt: str
    predicted_causality: str
    naranjo_metrics: NaranjoMetrics
    concordance_status: str


class Task(BaseModel):
    model_config = ConfigDict(extra="allow")
    case_id: str
    provenance: str = "supplied"
    clinical_profile: ClinicalProfile
    ai_predicted_output: AIPredictedOutput


class Dataset(BaseModel):
    model_config = ConfigDict(extra="allow")
    benchmark: str
    version: str
    tasks: List[Task] = Field(min_length=1)


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("/", " / ").split())


def grade_task(task: Task) -> Dict[str, Any]:
    profile, out = task.clinical_profile, task.ai_predicted_output
    truth = profile.ground_truth_causality

    flags: List[str] = []
    if out.concordance_status.strip() == DISCORDANT_LABEL:
        flags.append("discordant_label")
    if out.adverse_event_pt.strip() == UNSPECIFIED_TERM:
        flags.append("unspecified_adverse_event")

    diagnostics: List[str] = []
    if _norm(out.predicted_causality) in {_norm(x) for x in LAZY_WHO_UMC}:
        diagnostics.append("lazy_unclassified_label")
    if _norm(out.predicted_causality) != _norm(truth):
        diagnostics.append("who_umc_mismatch")
    if _norm(out.naranjo_metrics.category) != _norm(truth):
        diagnostics.append("naranjo_category_mismatch")
    if profile.regimen.concomitant_drugs.strip().lower() not in NO_CONCOMITANTS:
        diagnostics.append("multidrug_regimen")
        if flags:
            diagnostics.append("multidrug_regimen_failed")

    return {
        "case_id": task.case_id,
        "provenance": task.provenance,
        "suspect_drug": profile.regimen.suspect_drug,
        "concomitant_drugs": profile.regimen.concomitant_drugs,
        "failed": bool(flags),
        "flags": flags,
        "diagnostics": diagnostics,
        "expected_causality": truth,
        "predicted": {
            "adverse_event_pt": out.adverse_event_pt,
            "predicted_causality": out.predicted_causality,
            "naranjo_score": out.naranjo_metrics.score,
            "naranjo_category": out.naranjo_metrics.category,
        },
    }


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    try:
        with open(DATASET_PATH, "r", encoding="utf-8") as fh:
            dataset = Dataset.model_validate(json.load(fh))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"ERROR: could not load dataset: {exc}", file=sys.stderr)
        return 1

    results = [grade_task(t) for t in dataset.tasks]
    total = len(results)
    failed = sum(r["failed"] for r in results)
    passed = total - failed

    def count(key: str, field: str) -> int:
        return sum(key in r[field] for r in results)

    def subset(prov: str) -> Dict[str, Any]:
        rows = [r for r in results if r["provenance"] == prov]
        n = len(rows)
        f = sum(r["failed"] for r in rows)
        return {"cases": n, "failed": f, "failure_rate": round(f / n, 4) if n else None}

    by_provenance = {p: subset(p) for p in sorted({r["provenance"] for r in results})}
    multi_total = count("multidrug_regimen", "diagnostics")
    multi_failed = count("multidrug_regimen_failed", "diagnostics")

    report = {
        "benchmark": dataset.benchmark,
        "version": dataset.version,
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "accuracy_rate": round(passed / total, 4),
            "failure_rate": round(failed / total, 4),
            "discordant_label_count": count("discordant_label", "flags"),
            "unspecified_adverse_event_count": count("unspecified_adverse_event", "flags"),
            "lazy_unclassified_label_count": count("lazy_unclassified_label", "diagnostics"),
            "who_umc_mismatch_count": count("who_umc_mismatch", "diagnostics"),
            "naranjo_category_mismatch_count": count("naranjo_category_mismatch", "diagnostics"),
            "multidrug_cases": count("multidrug_regimen", "diagnostics"),
            "multidrug_cases_failed": multi_failed,
            "multidrug_failure_rate": round(multi_failed / multi_total, 4) if multi_total else None,
        },
        "by_provenance": by_provenance,
        "failed_case_ids": [r["case_id"] for r in results if r["failed"]],
        "cases": results,
    }
    atomic_write_json(REPORT_PATH, report)

    s = report["summary"]
    print(f"Total cases processed : {s['total_cases']}")
    print(f"Passed / Failed       : {s['passed']} / {s['failed']}")
    print(f"Accuracy rate         : {s['accuracy_rate'] * 100:.2f}%")
    print(f"Exposed failure rate  : {s['failure_rate'] * 100:.2f}%")
    if s["multidrug_failure_rate"] is not None:
        print(f"Multi-drug failures   : {s['multidrug_cases_failed']} / {s['multidrug_cases']} "
              f"({s['multidrug_failure_rate'] * 100:.2f}%)")
    print(f"Report written to     : {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
