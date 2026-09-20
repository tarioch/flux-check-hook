"""Run the hook with the real helm and kubectl against a chart repository served from localhost."""

import functools
import http.server
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

import pre_commit_flux.check_flux_helm_values as testm

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None or shutil.which("kubectl") is None,
    reason="needs helm and kubectl",
)

FIXTURES = Path(__file__).parent / "fixtures"

# the address of the chart repository in fixtures/flux/default/repository.yaml
REPOSITORY_URL = "http://localhost:8080"

# helm 3 reports the path of the invalid value as ingress.enabled, helm 4 as /ingress/enabled
VALUE_PATH = r"ingress[./]enabled"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def chart_repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The url of a helm chart repository that serves the chart in fixtures/chart."""
    directory = tmp_path_factory.mktemp("chart_repo")
    chart = FIXTURES / "chart"
    subprocess.run(
        ["helm", "package", str(chart), "--destination", str(directory)], check=True
    )
    subprocess.run(["helm", "repo", "index", str(directory)], check=True)

    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def no_errors() -> None:
    # the hook collects its errors in a module global
    testm.errors.clear()


@pytest.fixture(autouse=True)
def flux(tmp_path: Path, chart_repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Work in a copy of fixtures/flux, where the HelmRepository points to the chart repository."""
    shutil.copytree(FIXTURES / "flux", tmp_path, dirs_exist_ok=True)
    repository = tmp_path / "default" / "repository.yaml"
    repository.write_text(repository.read_text().replace(REPOSITORY_URL, chart_repo))
    monkeypatch.chdir(tmp_path)


def run_hook(*files: str) -> int | str | None:
    """Run the hook on the files and return its exit code."""
    with mock.patch("sys.argv", ["check-flux-helm-values", *files]):
        try:
            testm.main()
        except SystemExit as e:
            return e.code
    return 0


def test_basic_usecase(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook("default/release.yaml") == 0

    assert capsys.readouterr().err == ""


def test_invalid_values(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook("invalid_values/release.yaml") == 1

    out = capsys.readouterr().out
    assert "helm lint for" in out
    assert re.search(VALUE_PATH, out)


def test_unknown_chart_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook("unknown_version/release.yaml") == 1

    assert "helm pull for" in capsys.readouterr().out


def test_kustomization_detect(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook("kustomization/release.yaml") == 0

    assert "kustomization" in capsys.readouterr().out


def test_invalid_kustomization(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook("invalid_kustomization/release.yaml") == 1

    assert re.search(VALUE_PATH, capsys.readouterr().out)


def test_kustomization_in_a_directory_with_a_space(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_hook("kustomization with space/release.yaml") == 0

    assert "kustomization" in capsys.readouterr().out


def test_missing_helm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", "")

    assert run_hook("default/release.yaml") == 1

    out = capsys.readouterr().out
    assert "helm pull for" in out
    assert "not found" in out
