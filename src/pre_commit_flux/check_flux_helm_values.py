import glob
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# helm pull downloads the chart, this is the time a slow or unreachable repository gets
TIMEOUT_SECONDS = 300


def main():
    repos = _build_repo_map()
    errors = []
    for arg in sys.argv[1:]:
        try:
            _validate_file(arg, repos, errors)
        except Exception as ex:
            errors.append({"source": arg, "message": f"{type(ex).__name__} {ex.args}"})
    _print_errors(errors)
    return 1 if errors else 0


def _build_repo_map():
    repos = {}
    for file in glob.glob("./**/*.yaml", recursive=True):
        with open(file) as f:
            try:
                for definition in yaml.safe_load_all(f):
                    if (
                        not definition
                        or "kind" not in definition
                        or definition["kind"] != "HelmRepository"
                    ):
                        continue
                    repo_name = definition["metadata"]["name"]
                    repos[repo_name] = definition["spec"]["url"]
            except Exception:
                continue

    return repos


def _kustomize_release(directory, name):
    """The release of that name that the kustomization builds, or its only release."""
    kustomize_releases = []
    # the directory of the last iteration is "", that is the current directory
    res = _run(["kubectl", "kustomize", directory or "."])
    if res.returncode == 0:
        doc = str(res.stdout)
        for definition in yaml.safe_load_all(doc):
            if (
                definition
                and "kind" in definition
                and definition["kind"] == "HelmRelease"
            ):
                kustomize_releases.append(definition)

    for release in kustomize_releases:
        if release.get("metadata", {}).get("name") == name:
            return release
    return kustomize_releases[0] if len(kustomize_releases) == 1 else {}


def _validate_file(file_to_validate, repos, errors):
    with open(file_to_validate) as f:
        for definition in yaml.safe_load_all(f):
            if (
                not definition
                or "kind" not in definition
                or definition["kind"] != "HelmRelease"
            ):
                continue

            name = definition.get("metadata", {}).get("name")
            chart_spec = _chart_spec(definition)
            if not chart_spec and "chartRef" not in _spec(definition):
                # Maybe it kustomize
                path_to_file = f.name.split("/")
                while path_to_file:
                    path_to_file.pop()
                    file_dir = "/".join(path_to_file)
                    check = _kustomize_release(file_dir, name)
                    if check:
                        print(f"kustomization for {f.name} found {file_dir}")
                        definition = check
                        break

                chart_spec = _chart_spec(definition)

            if not chart_spec:
                if "chartRef" in _spec(definition):
                    print("Cannot validate OCI-based charts, skipping")
                else:
                    errors.append(
                        {
                            "source": file_to_validate,
                            "message": f"HelmRelease '{name}' has neither spec.chart.spec nor spec.chartRef "
                            "and no kustomization in the parent directories builds a HelmRelease of that name",
                        }
                    )
                continue

            if chart_spec["sourceRef"]["kind"] != "HelmRepository":
                continue

            chart_name = chart_spec["chart"]
            chart_version = chart_spec["version"]
            chart_url = repos.get(chart_spec["sourceRef"]["name"])
            if chart_url is None:
                errors.append(
                    {
                        "source": file_to_validate,
                        "message": f"HelmRepository '{chart_spec['sourceRef']['name']}' is not defined "
                        "in a yaml file below the current directory",
                    }
                )
                continue

            with tempfile.TemporaryDirectory() as tmp_dir:
                with open(Path(tmp_dir) / "values.yaml", "w") as values_file:
                    if "spec" in definition and "values" in definition["spec"]:
                        yaml.safe_dump(definition["spec"]["values"], values_file)

                if chart_url.startswith("oci://"):
                    chart_oci_url = f"{chart_url}{'' if chart_url.endswith('/') else '/'}{chart_name}"
                    command = [
                        "helm",
                        "pull",
                        chart_oci_url,
                        "--version",
                        chart_version,
                    ]
                else:
                    command = [
                        "helm",
                        "pull",
                        "--repo",
                        chart_url,
                        "--version",
                        chart_version,
                        chart_name,
                    ]

                res = _run(command, cwd=tmp_dir)
                if res.returncode != 0:
                    errors.append(
                        {
                            "source": f"helm pull for '{file_to_validate}'",
                            "message": f"\n{res.stdout}",
                        }
                    )
                    continue

                charts = sorted(glob.glob("*.tgz", root_dir=tmp_dir))
                res = _run(["helm", "lint", "-f", "values.yaml", *charts], cwd=tmp_dir)
                if res.returncode != 0:
                    errors.append(
                        {
                            "source": f"helm lint for '{file_to_validate}'",
                            "message": f"\n{res.stdout}",
                        }
                    )


def _spec(definition):
    return definition.get("spec") or {}


def _chart_spec(definition):
    """The chart of a HelmRelease, None if it has no spec.chart.spec."""
    return (_spec(definition).get("chart") or {}).get("spec")


def _run(command, cwd=None):
    """Run a command, its stdout and stderr are the output."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        # what a shell reports for a command it does not find
        return subprocess.CompletedProcess(
            command, 127, f"{command[0]}: command not found\n"
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            f"{' '.join(command)}: timed out after {TIMEOUT_SECONDS} seconds\n",
        )


def _print_errors(errors):
    for error in errors:
        print(f"[ERROR] {error['source']}: {error['message']}")


if __name__ == "__main__":
    raise SystemExit(main())
