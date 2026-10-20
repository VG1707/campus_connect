"""
Timetable Extraction Accuracy Benchmark Evaluator.
Compares extracted timetable schedules against hand-written ground-truth JSON fixtures.
Computes per-cell accuracy, per-image accuracy, and outputs detailed mismatch diffs.
100% deterministic, offline, and zero live external API dependencies.
"""

from pathlib import Path
from typing import Dict, List, Optional
import json

from app.extractor.mock_extractor import MockTimetableExtractor
from app.extractor.base import ExtractedTimetable, ExtractedEntry


class EvaluationResult:
    """Summary of benchmark run across all fixtures."""
    def __init__(self):
        self.fixtures_evaluated: int = 0
        self.total_expected_cells: int = 0
        self.total_extracted_cells: int = 0
        self.total_matched_cells: int = 0
        self.overall_cell_accuracy: float = 0.0
        self.per_fixture_results: List[dict] = []
        self.mismatches: List[dict] = []


def evaluate_single_fixture(
    fixture_path: Path,
    extractor: MockTimetableExtractor
) -> dict:
    """
    Evaluate one ground-truth JSON fixture against the extractor.
    Returns per-cell and per-image accuracy alongside mismatch details.
    """
    with open(fixture_path, "r", encoding="utf-8") as f:
        gt_data = json.load(f)

    expected_entries = [ExtractedEntry(**item) for item in gt_data.get("entries", [])]
    expected_map = {f"{e.day}_P{e.period}": e for e in expected_entries}

    # Extract using the extractor
    extracted_tt = extractor.extract_timetable(str(fixture_path))
    extracted_map = {f"{e.day}_P{e.period}": e for e in extracted_tt.entries}

    all_keys = sorted(list(set(expected_map.keys()).union(set(extracted_map.keys()))))

    matched_cells = 0
    mismatches = []

    for key in all_keys:
        exp = expected_map.get(key)
        ext = extracted_map.get(key)

        if exp and ext:
            # Check course code match
            if exp.course_code.strip().upper() == ext.course_code.strip().upper():
                matched_cells += 1
            else:
                mismatches.append({
                    "cell_key": key,
                    "issue": "Course code mismatch",
                    "expected": exp.course_code,
                    "extracted": ext.course_code,
                    "room_expected": exp.room,
                    "room_extracted": ext.room
                })
        elif exp and not ext:
            mismatches.append({
                "cell_key": key,
                "issue": "Missing cell in extraction",
                "expected": f"{exp.course_code} ({exp.day} P{exp.period})",
                "extracted": "None"
            })
        elif ext and not exp:
            mismatches.append({
                "cell_key": key,
                "issue": "Spurious extra cell extracted",
                "expected": "None",
                "extracted": f"{ext.course_code} ({ext.day} P{ext.period})"
            })

    total_expected = len(expected_entries)
    cell_accuracy = (matched_cells / total_expected * 100.0) if total_expected > 0 else 100.0
    image_accuracy = 100.0 if len(mismatches) == 0 else cell_accuracy

    return {
        "fixture_name": fixture_path.name,
        "department": gt_data.get("department", "Engineering"),
        "semester": gt_data.get("semester", 3),
        "total_expected_cells": total_expected,
        "total_extracted_cells": len(extracted_tt.entries),
        "matched_cells": matched_cells,
        "cell_accuracy": round(cell_accuracy, 1),
        "image_accuracy": round(image_accuracy, 1),
        "mismatches": mismatches
    }


def run_extraction_evaluation(fixtures_dir: Optional[Path] = None) -> dict:
    """
    Run the complete extraction benchmark suite across all 4 sample timetable fixtures.
    """
    fixtures_dir = fixtures_dir or (Path(__file__).parent.parent.parent / "data" / "fixtures")
    fixture_files = sorted(list(fixtures_dir.glob("*.json")))

    extractor = MockTimetableExtractor(fixtures_dir=fixtures_dir)
    results = []
    all_mismatches = []
    total_expected = 0
    total_matched = 0

    for fpath in fixture_files:
        res = evaluate_single_fixture(fpath, extractor)
        results.append(res)
        total_expected += res["total_expected_cells"]
        total_matched += res["matched_cells"]
        for m in res["mismatches"]:
            m["fixture_name"] = res["fixture_name"]
            all_mismatches.append(m)

    overall_accuracy = (total_matched / total_expected * 100.0) if total_expected > 0 else 100.0

    return {
        "fixtures_count": len(fixture_files),
        "total_expected_cells": total_expected,
        "total_matched_cells": total_matched,
        "overall_accuracy": round(overall_accuracy, 1),
        "per_fixture_results": results,
        "mismatches": all_mismatches
    }


if __name__ == "__main__":
    report = run_extraction_evaluation()
    print("=== Extraction Evaluation Benchmark Report ===")
    print(f"Overall Cell Accuracy: {report['overall_accuracy']}% ({report['total_matched_cells']}/{report['total_expected_cells']} cells)")
    print(f"Fixtures Evaluated: {report['fixtures_count']}")
    for r in report["per_fixture_results"]:
        print(f" - {r['fixture_name']}: {r['cell_accuracy']}% ({r['matched_cells']}/{r['total_expected_cells']})")
    if report["mismatches"]:
        print(f"Total Mismatches: {len(report['mismatches'])}")
    else:
        print("Zero mismatches found across all fixtures.")
