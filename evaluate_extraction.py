"""
CampusConnect - Standalone Timetable Extraction Benchmark CLI Script.
Usage:
    python evaluate_extraction.py
Runs the deterministic benchmark against all ground-truth JSON fixtures in data/fixtures/.
Reports per-cell accuracy, per-image accuracy, and outputs detailed diffs.
"""

from app.extractor.evaluator import run_extraction_evaluation


def main():
    report = run_extraction_evaluation()
    print("=" * 65)
    print("      CAMPUSCONNECT: TIMETABLE EXTRACTION BENCHMARK")
    print("=" * 65)
    print(f"Total Fixtures Evaluated : {report['fixtures_count']}")
    print(f"Overall Cell Accuracy   : {report['overall_accuracy']}%")
    print(f"Cells Matched / Expected: {report['total_matched_cells']} / {report['total_expected_cells']}")
    print("-" * 65)
    print(f"{'Fixture Name':<32} {'Department':<20} {'Accuracy':<10}")
    print("-" * 65)
    for r in report["per_fixture_results"]:
        dept_abbr = r["department"].split('&')[0].strip()
        print(f"{r['fixture_name']:<32} {dept_abbr:<20} {r['cell_accuracy']}%")
    print("-" * 65)

    if report["mismatches"]:
        print("\nMISMATCHES FOUND:")
        for m in report["mismatches"]:
            print(f"  [{m['fixture_name']}] {m['cell_key']}: Expected '{m['expected']}' vs Got '{m['extracted']}' ({m['issue']})")
    else:
        print("\nSUCCESS: All ground-truth timetable cells matched with 100% precision.")
    print("=" * 65)


if __name__ == "__main__":
    main()
