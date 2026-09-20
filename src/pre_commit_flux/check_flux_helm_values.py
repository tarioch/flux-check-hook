"""Check that the values of flux HelmReleases are valid for the chart they are used with."""

import glob
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import yaml

TIMEOUT_SECONDS = 300

Document = dict[str, Any]


class Problem(NamedTuple):
    source: str
    message: str


def main(argv: Sequence[str] | None = None) -> int:
    files = sys.argv[1:] if argv is None else argv
    repos = build_repo_map()
    problems: list[Problem] = []
    for file in files:
        try:
            problems.extend(validate_file(Path(file), repos))
        except Exception as ex:  # a broken file must not stop the check of the others
            problems.append(Problem(file, f"{type(ex).__name__} {ex.args}"))
    for problem in problems:
        print(f"[ERROR] {problem.source}: {problem.message}")
    return 1 if problems else 0


def build_repo_map() -> dict[str, str]:
    """Map the name of every HelmRepository below the current directory to its url."""
    repos: dict[str, str] = {}
    for file in glob.glob("**/*.yaml", recursive=True):
        try:
            documents = _load_documents(Path(file))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        for repository in _of_kind(documents, "HelmRepository"):
            name = _get(repository, "metadata", "name")
            url = _get(repository, "spec", "url")
            if name and url:
                repos[name] = url
    return repos


def validate_file(path: Path, repos: dict[str, str]) -> list[Problem]:
    problems: list[Problem] = []
    for release in _of_kind(_load_documents(path), "HelmRelease"):
        if not _has_chart(release):
            release = _from_kustomization(path, release) or release
        spec = release.get("spec") or {}
        chart_spec = _get(spec, "chart", "spec")
        if not chart_spec:
            if "chartRef" in spec:
                print("Cannot validate OCI-based charts, skipping")
            else:
                problems.append(_no_chart(path, release))
            continue
        problems.extend(_lint(str(path), chart_spec, spec.get("values"), repos))
    return problems


def _load_documents(path: Path) -> list[Any]:
    return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))


def _of_kind(documents: Sequence[Any], kind: str) -> Iterator[Document]:
    for document in documents:
        if isinstance(document, dict) and document.get("kind") == kind:
            yield document


def _get(document: Document, *keys: str) -> Any:
    """Follow the keys down a nested document, None if any of them is missing."""
    value: Any = document
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _has_chart(release: Document) -> bool:
    return bool(
        _get(release, "spec", "chart", "spec") or _get(release, "spec", "chartRef")
    )


def _no_chart(path: Path, release: Document) -> Problem:
    name = _get(release, "metadata", "name")
    return Problem(
        str(path),
        f"HelmRelease '{name}' has neither spec.chart.spec nor spec.chartRef and no kustomization "
        "in the parent directories builds it (a patch needs `kubectl kustomize` to find its base)",
    )


def _from_kustomization(path: Path, patch: Document) -> Document | None:
    """Find the complete release for a patch: the one of the same name built by a kustomization."""
    name = _get(patch, "metadata", "name")
    for directory in (path.parent, *path.parent.parents):
        releases = _kustomize_releases(directory)
        if not releases:
            continue
        print(f"kustomization for {path} found {directory}")
        named = [
            release for release in releases if _get(release, "metadata", "name") == name
        ]
        if named:
            return named[0]
        return releases[0] if len(releases) == 1 else None
    return None


def _kustomize_releases(directory: Path) -> list[Document]:
    result = _run(["kubectl", "kustomize", str(directory)])
    if result.returncode != 0:
        return []
    return list(_of_kind(list(yaml.safe_load_all(result.stdout)), "HelmRelease"))


def _lint(
    source: str, chart_spec: Document, values: Any, repos: dict[str, str]
) -> list[Problem]:
    source_ref = chart_spec["sourceRef"]
    if source_ref["kind"] != "HelmRepository":
        return []

    chart = chart_spec["chart"]
    version = str(chart_spec["version"])
    url = repos.get(source_ref["name"])
    if url is None:
        return [
            Problem(
                source,
                f"HelmRepository '{source_ref['name']}' is not defined in a yaml file below the "
                "current directory",
            )
        ]

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        (work_dir / "values.yaml").write_text(
            "" if values is None else yaml.safe_dump(values)
        )

        if url.startswith("oci://"):
            pull = ["helm", "pull", f"{url.rstrip('/')}/{chart}", "--version", version]
        else:
            pull = ["helm", "pull", "--repo", url, "--version", version, chart]
        result = _run(pull, cwd=work_dir)
        if result.returncode != 0:
            return [Problem(f"helm pull for '{source}'", f"\n{result.stdout}")]

        archives = sorted(path.name for path in work_dir.glob("*.tgz"))
        result = _run(["helm", "lint", "-f", "values.yaml", *archives], cwd=work_dir)
        if result.returncode != 0:
            return [Problem(f"helm lint for '{source}'", f"\n{result.stdout}")]
    return []


def _run(
    command: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command, stdout and stderr come back as text, failing to run it is an exit code."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            command, 127, f"{command[0]}: command not found\n"
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            f"{' '.join(command)}: timed out after {TIMEOUT_SECONDS} seconds\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())
