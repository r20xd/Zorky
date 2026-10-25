"""CI: каждый push в main получает свой запуск, запуски коммитов не отменяют друг друга;
гейты гоняются для тех же зон, что у gate-changed."""

from pathlib import Path

import yaml

import tasks

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def concurrency_problem(workflow: dict) -> str | None:
    """Текст проблемы или None, если запуски разных коммитов не гасят друг друга."""
    conc = workflow.get("concurrency")
    if conc is None:
        return None
    if isinstance(conc, str):
        return None if "github.sha" in conc else "группа concurrency без github.sha"
    group = str(conc.get("group", ""))
    if "github.sha" in group:
        return None
    if conc.get("cancel-in-progress"):
        return f"cancel-in-progress при общей группе {group!r}: push гасит прошлый запуск"
    return None


def test_ci_yaml_is_valid_and_has_jobs():
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict) and workflow.get("jobs")


def test_ci_does_not_cancel_runs_of_other_commits():
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert concurrency_problem(workflow) is None


def test_checker_flags_old_config():
    old = {"concurrency": {"group": "ci-${{ github.ref }}", "cancel-in-progress": True}}
    assert concurrency_problem(old) is not None


def test_ci_runs_gates_of_same_zones_as_gate_changed():
    """Фильтр CI = ZONE_PREFIXES: иначе gate-changed и CI гоняют разные гейты."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["changes"]["steps"] if s.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    ci = {zone: sorted(p.removesuffix("**") for p in paths) for zone, paths in filters.items()}
    local = {
        zone: sorted(prefix for prefix, zones in tasks.ZONE_PREFIXES.items() if zone in zones)
        for zone in tasks.GATES
    }
    assert ci == local
