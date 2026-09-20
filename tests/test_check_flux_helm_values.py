"""Unit tests, helm and kubectl are replaced by a fake, see test_integration.py for the real ones."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pre_commit_flux import check_flux_helm_values as hook

REPOSITORY = """\
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: charts
spec:
  url: {url}
"""

RELEASE = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: {name}
spec:
  chart:
    spec:
      chart: demo
      version: 1.2.3
      sourceRef:
        kind: {source_kind}
        name: charts
  values:
    ingress:
      enabled: {enabled}
"""

Handler = Callable[[list[str]], tuple[int, str]]


class FakeRun:
    """Stands in for hook._run: records the commands and answers them with the handler."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler = handler or (lambda command: (0, ""))
        self.commands: list[list[str]] = []
        self.values: list[str] = []

    def __call__(
        self, command: list[str], cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        returncode, output = self.handler(command)
        if command[:2] == ["helm", "pull"] and returncode == 0:
            assert cwd is not None
            (cwd / "demo-1.2.3.tgz").touch()
        if command[:2] == ["helm", "lint"]:
            assert cwd is not None
            self.values.append((cwd / "values.yaml").read_text())
        return subprocess.CompletedProcess(command, returncode, output)

    def with_prefix(self, *prefix: str) -> list[list[str]]:
        return [
            command
            for command in self.commands
            if command[: len(prefix)] == list(prefix)
        ]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "repository.yaml").write_text(
        REPOSITORY.format(url="https://charts.example.org")
    )
    return tmp_path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    run = FakeRun()
    monkeypatch.setattr(hook, "_run", run)
    return run


def write_release(
    directory: Path,
    name: str = "demo",
    enabled: str = "true",
    source_kind: str = "HelmRepository",
) -> str:
    (directory / "release.yaml").write_text(
        RELEASE.format(name=name, enabled=enabled, source_kind=source_kind)
    )
    return "release.yaml"


def test_repo_map_collects_helm_repositories(project: Path) -> None:
    (project / "nested").mkdir()
    (project / "nested" / "more.yaml").write_text(
        "kind: HelmRepository\nmetadata:\n  name: other\nspec:\n  url: oci://registry.example/x\n"
        "---\nkind: HelmRepository\nmetadata:\n  name: without-url\n"
        "---\nkind: ConfigMap\nmetadata:\n  name: not-a-repository\n"
    )
    (project / "broken.yaml").write_text("a: [unclosed")

    assert hook.build_repo_map() == {
        "charts": "https://charts.example.org",
        "other": "oci://registry.example/x",
    }


def test_release_is_pulled_and_linted_with_its_values(
    project: Path, fake: FakeRun
) -> None:
    assert hook.main([write_release(project)]) == 0

    assert fake.with_prefix("helm", "pull") == [
        [
            "helm",
            "pull",
            "--repo",
            "https://charts.example.org",
            "--version",
            "1.2.3",
            "demo",
        ]
    ]
    assert fake.with_prefix("helm", "lint") == [
        ["helm", "lint", "-f", "values.yaml", "demo-1.2.3.tgz"]
    ]
    assert fake.values == ["ingress:\n  enabled: true\n"]


def test_oci_repository_is_pulled_from_the_chart_url(
    project: Path, fake: FakeRun
) -> None:
    (project / "repository.yaml").write_text(
        REPOSITORY.format(url="oci://registry.example/charts/")
    )

    assert hook.main([write_release(project)]) == 0

    assert fake.with_prefix("helm", "pull") == [
        ["helm", "pull", "oci://registry.example/charts/demo", "--version", "1.2.3"]
    ]


def test_charts_of_other_sources_are_not_checked(project: Path, fake: FakeRun) -> None:
    assert hook.main([write_release(project, source_kind="GitRepository")]) == 0

    assert fake.commands == []


def test_chart_ref_is_skipped(
    project: Path, fake: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "release.yaml").write_text(
        "kind: HelmRelease\nmetadata:\n  name: demo\nspec:\n  chartRef:\n    kind: OCIRepository\n"
        "    name: demo\n"
    )

    assert hook.main(["release.yaml"]) == 0

    assert "skipping" in capsys.readouterr().out
    # a release with a chartRef is complete, no kustomization is needed to find its chart
    assert fake.commands == []


def test_files_without_a_helm_release_are_ignored(project: Path, fake: FakeRun) -> None:
    assert hook.main(["repository.yaml"]) == 0

    assert fake.commands == []


def test_unknown_repository_is_reported(
    project: Path, fake: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "repository.yaml").unlink()

    assert hook.main([write_release(project)]) == 1

    assert "HelmRepository 'charts' is not defined" in capsys.readouterr().out
    assert fake.commands == []


@pytest.mark.parametrize("step", ["pull", "lint"])
def test_failing_helm_is_reported_with_its_output(
    step: str, project: Path, fake: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.handler = lambda command: (
        (1, "the helm output") if command[1] == step else (0, "")
    )

    assert hook.main([write_release(project)]) == 1

    assert (
        f"[ERROR] helm {step} for 'release.yaml': \nthe helm output"
        in capsys.readouterr().out
    )


def test_problems_do_not_carry_over_to_the_next_run(
    project: Path, fake: FakeRun
) -> None:
    fake.handler = lambda command: (
        (1, "") if command[:2] == ["helm", "lint"] else (0, "")
    )
    assert hook.main([write_release(project)]) == 1

    fake.handler = lambda command: (0, "")
    assert hook.main([write_release(project)]) == 0


def test_a_broken_file_does_not_stop_the_others(
    project: Path, fake: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "broken.yaml").write_text("a: [unclosed")
    fake.handler = lambda command: (
        (1, "invalid") if command[:2] == ["helm", "lint"] else (0, "")
    )

    assert hook.main(["broken.yaml", write_release(project)]) == 1

    out = capsys.readouterr().out
    assert "[ERROR] broken.yaml: ParserError" in out
    assert "[ERROR] helm lint for 'release.yaml'" in out


def test_missing_binaries_are_reported(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_such_command(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", no_such_command)

    assert hook.main([write_release(project)]) == 1

    assert "helm: command not found" in capsys.readouterr().out


class TestKustomization:
    """A patch has no chart, the complete release is built by a kustomization further up."""

    BUILT = RELEASE.format(
        name="{name}", enabled="{enabled}", source_kind="HelmRepository"
    )

    @pytest.fixture
    def overlay(self, project: Path) -> str:
        (project / "overlay").mkdir()
        (project / "overlay" / "release.yaml").write_text(
            "kind: HelmRelease\nmetadata:\n  name: demo\nspec:\n  values:\n    a: b\n"
        )
        return "overlay/release.yaml"

    def kustomize(self, fake: FakeRun, directory: str, *releases: str) -> None:
        def handler(command: list[str]) -> tuple[int, str]:
            if command[:2] == ["kubectl", "kustomize"] and Path(command[2]) == Path(
                directory
            ):
                return 0, "---\n".join(releases)
            return (1, "no kustomization") if command[0] == "kubectl" else (0, "")

        fake.handler = handler

    def test_the_release_of_the_same_name_is_checked(
        self,
        project: Path,
        fake: FakeRun,
        overlay: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.kustomize(
            fake,
            ".",
            self.BUILT.format(name="other", enabled="false"),
            self.BUILT.format(name="demo", enabled="true"),
            self.BUILT.format(name="another", enabled="false"),
        )

        assert hook.main([overlay]) == 0

        assert "kustomization for" in capsys.readouterr().out
        assert fake.values == ["ingress:\n  enabled: true\n"]
        # the directories are tried from the patch upwards
        assert [
            Path(command[2]) for command in fake.with_prefix("kubectl", "kustomize")
        ] == [
            Path("overlay"),
            Path("."),
        ]

    def test_a_single_release_is_used_even_if_the_name_differs(
        self, project: Path, fake: FakeRun, overlay: str
    ) -> None:
        self.kustomize(
            fake, "overlay", self.BUILT.format(name="renamed", enabled="true")
        )

        assert hook.main([overlay]) == 0

        assert len(fake.with_prefix("helm", "lint")) == 1

    def test_no_release_of_that_name_among_several_is_reported(
        self,
        project: Path,
        fake: FakeRun,
        overlay: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.kustomize(
            fake,
            "overlay",
            self.BUILT.format(name="one", enabled="true"),
            self.BUILT.format(name="two", enabled="true"),
        )

        assert hook.main([overlay]) == 1

        assert "HelmRelease 'demo' has neither" in capsys.readouterr().out
        assert fake.with_prefix("helm") == []

    def test_a_patch_without_a_kustomization_is_reported(
        self,
        project: Path,
        fake: FakeRun,
        overlay: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake.handler = lambda command: (1, "") if command[0] == "kubectl" else (0, "")

        assert hook.main([overlay]) == 1

        assert "HelmRelease 'demo' has neither" in capsys.readouterr().out
