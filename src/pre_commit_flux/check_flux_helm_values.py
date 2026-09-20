import glob
import os.path as path
import subprocess
import sys
import tempfile

import yaml


def main():
    repos = _buildRepoMap()
    errors = []
    for arg in sys.argv[1:]:
        try:
            _validateFile(arg, repos, errors)
        except Exception as ex:
            errors.append({"source": arg, "message": f"{type(ex).__name__} {ex.args}"})
    _printErrors(errors)
    return 1 if errors else 0


def _buildRepoMap():
    repos = {}
    for file in glob.glob("./**/*.yaml", recursive=True):
        with open(file) as f:
            try:
                for definition in yaml.load_all(f, Loader=yaml.SafeLoader):
                    if (
                        not definition
                        or "kind" not in definition
                        or definition["kind"] != "HelmRepository"
                    ):
                        continue
                    repoName = definition["metadata"]["name"]
                    repos[repoName] = definition["spec"]["url"]
            except Exception:
                continue

    return repos


def check_kustomiztion(path: str):
    kustomize_release = {}
    # the directory of the last iteration is "", that is the current directory
    res = _run(["kubectl", "kustomize", path or "."])
    if res.returncode == 0:
        doc = str(res.stdout)
        for definition in yaml.load_all(doc, Loader=yaml.SafeLoader):
            if (
                definition
                and "kind" in definition
                and definition["kind"] == "HelmRelease"
            ):
                kustomize_release = definition

    return kustomize_release


def _validateFile(fileToValidate, repos, errors):
    with open(fileToValidate) as f:
        for definition in yaml.load_all(f, Loader=yaml.SafeLoader):
            if (
                not definition
                or "kind" not in definition
                or definition["kind"] != "HelmRelease"
            ):
                continue

            try:
                chartSpec = definition["spec"]["chart"]["spec"]

            except KeyError:
                # Maybe it kustomize
                path_to_file = f.name.split("/")
                while path_to_file:
                    path_to_file.pop()
                    fileDir = "/".join(path_to_file)
                    check = check_kustomiztion(fileDir)
                    if check:
                        print(f"kustomization for {f.name} found {fileDir}")
                        definition = check
                        break

                try:
                    chartSpec = definition["spec"]["chart"]["spec"]

                except KeyError as e:
                    if definition["spec"]["chartRef"]:
                        print("Cannot validate OCI-based charts, skipping")
                        continue
                    else:
                        raise e

            if chartSpec["sourceRef"]["kind"] != "HelmRepository":
                continue

            chartName = chartSpec["chart"]
            chartVersion = chartSpec["version"]
            chartUrl = repos[chartSpec["sourceRef"]["name"]]

            with tempfile.TemporaryDirectory() as tmpDir:
                with open(path.join(tmpDir, "values.yaml"), "w") as valuesFile:
                    if "spec" in definition and "values" in definition["spec"]:
                        yaml.dump(definition["spec"]["values"], valuesFile)

                if chartUrl.startswith("oci://"):
                    chartOciUrl = (
                        f"{chartUrl}{'' if chartUrl.endswith('/') else '/'}{chartName}"
                    )
                    command = ["helm", "pull", chartOciUrl, "--version", chartVersion]
                else:
                    command = [
                        "helm",
                        "pull",
                        "--repo",
                        chartUrl,
                        "--version",
                        chartVersion,
                        chartName,
                    ]

                res = _run(command, cwd=tmpDir)
                if res.returncode != 0:
                    errors.append(
                        {
                            "source": f"helm pull for '{fileToValidate}'",
                            "message": f"\n{res.stdout}",
                        }
                    )
                    continue

                charts = sorted(glob.glob("*.tgz", root_dir=tmpDir))
                res = _run(["helm", "lint", "-f", "values.yaml", *charts], cwd=tmpDir)
                if res.returncode != 0:
                    errors.append(
                        {
                            "source": f"helm lint for '{fileToValidate}'",
                            "message": f"\n{res.stdout}",
                        }
                    )


def _run(command, cwd=None):
    """Run a command, its stdout and stderr are the output."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        # what a shell reports for a command it does not find
        return subprocess.CompletedProcess(
            command, 127, f"{command[0]}: command not found\n"
        )


def _printErrors(errors):
    for i in errors:
        print(f"[ERROR] {i['source']}: {i['message']}")


if __name__ == "__main__":
    raise SystemExit(main())
