#!/usr/bin/env python3
"""
Check for code coverage duplication across repositories.

This script analyzes a metrics run to detect if multiple repositories
have identical code coverage values, which would indicate a data
aggregation bug.

Usage:
    python3 scripts/check_coverage_duplication.py exports/LATEST/
"""

import json
import sys
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List


def load_maturity_assessment(export_dir: Path) -> Dict:
    """Load the maturity assessment JSON file."""
    maturity_file = export_dir / "metrics_maturity_assessment.json"

    if not maturity_file.exists():
        print(f"❌ Error: {maturity_file} not found")
        sys.exit(1)

    with open(maturity_file, 'r') as f:
        return json.load(f)


def analyze_coverage_duplication(data: Dict) -> None:
    """Analyze and report code coverage duplication by project."""

    repositories = data.get('repositories', {})

    if not repositories:
        print("❌ No repositories found in maturity assessment")
        sys.exit(1)

    print(f"📊 Analyzing {len(repositories)} repositories\n")
    print("=" * 70)

    # Group repos by project
    project_repos: Dict[str, List[Dict]] = defaultdict(list)

    for repo_id, repo in repositories.items():
        proj_name = repo.get('project', {}).get('name', 'Unknown')
        coverage = repo.get('quality_assurance', {}).get('metrics', {}).get('code_coverage')

        project_repos[proj_name].append({
            'name': repo.get('name'),
            'coverage': coverage,
            'test_pass_rate': repo.get('quality_assurance', {}).get('metrics', {}).get('test_pass_rate'),
            'build_success_rate': repo.get('quality_assurance', {}).get('metrics', {}).get('build_success_rate')
        })

    # Analyze each project
    total_projects = len(project_repos)
    projects_with_duplication = 0
    total_duplicated_repos = 0

    for proj_name in sorted(project_repos.keys()):
        repos = project_repos[proj_name]

        # Get non-null coverage values
        coverage_values = [r['coverage'] for r in repos if r['coverage'] is not None]

        if not coverage_values:
            continue

        # Count duplicates
        coverage_counter = Counter(coverage_values)

        # Find values that appear multiple times
        duplicates = {cov: count for cov, count in coverage_counter.items() if count > 2}

        if duplicates:
            projects_with_duplication += 1

            print(f"\n⚠️  PROJECT: {proj_name}")
            print(f"   Total repos: {len(repos)}")
            print(f"   Repos with coverage: {len(coverage_values)}")
            print(f"   Unique coverage values: {len(set(coverage_values))}")
            print(f"\n   DUPLICATE COVERAGE VALUES:")

            for cov_value, count in sorted(duplicates.items(), key=lambda x: x[1], reverse=True):
                print(f"      {cov_value:.14f}%: {count} repos")
                total_duplicated_repos += count

                # Show sample repos
                matching_repos = [r for r in repos if r['coverage'] == cov_value]
                print(f"      Sample repos:")
                for r in matching_repos[:5]:
                    print(f"        - {r['name']}")
                    print(f"            coverage: {r['coverage']}, test_pass: {r['test_pass_rate']}")

                if len(matching_repos) > 5:
                    print(f"        ... and {len(matching_repos) - 5} more")

            print("-" * 70)

    # Summary
    print("\n" + "=" * 70)
    print("📈 SUMMARY")
    print("=" * 70)

    if projects_with_duplication == 0:
        print("✅ NO DUPLICATION DETECTED")
        print("   All repositories have unique coverage values (or reasonably unique)")
    else:
        print(f"⚠️  DUPLICATION DETECTED!")
        print(f"   Projects with duplication: {projects_with_duplication} / {total_projects}")
        print(f"   Total repos affected: {total_duplicated_repos}")
        print()
        print("   This indicates a potential bug in coverage calculation.")
        print("   Coverage values should not be identical across multiple repos")
        print("   unless they truly have the exact same coverage (very rare).")

    print("=" * 70)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_coverage_duplication.py exports/LATEST/")
        sys.exit(1)

    export_dir = Path(sys.argv[1])

    if not export_dir.exists():
        print(f"❌ Error: Directory {export_dir} not found")
        sys.exit(1)

    print(f"🔍 Checking coverage duplication in: {export_dir}\n")

    data = load_maturity_assessment(export_dir)
    analyze_coverage_duplication(data)

    print("\n✅ Analysis complete")


if __name__ == "__main__":
    main()
