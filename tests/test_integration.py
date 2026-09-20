"""Run the hook with the real helm and kubectl against a chart repository served from localhost."""

import functools
import http.server
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pre_commit_flux import check_flux_helm_values as hook

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None or shutil.which("kubectl") is None,
    reason="needs helm and kubectl",
)

CHART = Path(__file__).parent / "fixtures" / "chart"

# helm 3 reports the path of the invalid value as ingress.enabled, helm 4 as /ingress/enabled
VALUE_PATH = r"ingress[./]enabled"

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
  name: demo
spec:
  interval: 1h
  chart:
    spec:
      chart: demo
      version: 1.2.3
      sourceRef:
        kind: HelmRepository
        name: charts
  values:
    ingress:
      enabled: {enabled}
"""

PATCH = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: demo
spec:
  values:
    ingress:
      enabled: {enabled}
"""

KUSTOMIZATION = """\
resources:
  - ../base
patches:
  - path: release.yaml
    target:
      kind: HelmRelease
      name: demo
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def chart_repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The url of a helm chart repository that serves the chart in fixtures/chart."""
    directory = tmp_path_factory.mktemp("chart_repo")
    subprocess.run(
        ["helm", "package", str(CHART), "--destination", str(directory)], check=True
    )
    subprocess.run(["helm", "repo", "index", str(directory)], check=True)

    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def base(tmp_path: Path, chart_repo: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "base"
    directory.mkdir()
    (directory / "repository.yaml").write_text(REPOSITORY.format(url=chart_repo))
    (directory / "release.yaml").write_text(RELEASE.format(enabled="true"))
    (directory / "kustomization.yaml").write_text(
        "resources:\n  - repository.yaml\n  - release.yaml\n"
    )
    return directory


@pytest.fixture
def overlay(base: Path) -> Path:
    directory = base.parent / "overlay"
    directory.mkdir()
    (directory / "kustomization.yaml").write_text(KUSTOMIZATION)
    return directory


def test_valid_values_pass(base: Path) -> None:
    assert hook.main(["base/release.yaml"]) == 0


def test_values_that_violate_the_chart_schema_fail(
    base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (base / "release.yaml").write_text(RELEASE.format(enabled="8"))

    assert hook.main(["base/release.yaml"]) == 1

    out = capsys.readouterr().out
    assert "helm lint for" in out
    assert re.search(VALUE_PATH, out)


def test_an_unknown_chart_version_fails_the_pull(
    base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (base / "release.yaml").write_text(
        RELEASE.format(enabled="true").replace("1.2.3", "9.9.9")
    )

    assert hook.main(["base/release.yaml"]) == 1

    assert "helm pull for" in capsys.readouterr().out


def test_a_valid_patch_passes(
    overlay: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (overlay / "release.yaml").write_text(PATCH.format(enabled="false"))

    assert hook.main(["overlay/release.yaml"]) == 0

    assert "kustomization for" in capsys.readouterr().out


def test_an_invalid_patch_fails(
    overlay: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (overlay / "release.yaml").write_text(PATCH.format(enabled="8"))

    assert hook.main(["overlay/release.yaml"]) == 1

    assert re.search(VALUE_PATH, capsys.readouterr().out)
