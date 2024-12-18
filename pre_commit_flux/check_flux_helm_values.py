import glob
import os.path as path
import subprocess
import sys
import tempfile
from shlex import quote

import yaml

errors: list = []


def main():
    repos = _buildRepoMap()
    for arg in sys.argv[1:]:
        try:
            _validateFile(arg, repos)
        except Exception as ex:
            _collectErrors({"source": arg, "message": f"{type(ex).__name__} {ex.args}"})
    if len(errors) > 0:
        _printErrors()
        exit(1)


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


def _validateFile(fileToValidate, repos):
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

                command = f"helm pull --repo {quote(chartUrl)} --version {quote(chartVersion)} {quote(chartName)}"
                
                res = subprocess.run(
                    command,
                    shell=True,
                    cwd=tmpDir,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                if res.returncode != 0:
                    _collectErrors(
                        {"source": "helm pull", "message": f"\n{res.stdout}"}
                    )
                    continue

                res = subprocess.run(
                    "helm lint -f values.yaml *.tgz",
                    shell=True,
                    cwd=tmpDir,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                if res.returncode != 0:
                    _collectErrors(
                        {"source": "helm lint", "message": f"\n{res.stdout}"}
                    )


def _collectErrors(error):
    errors.append(error)


def _printErrors():
    for i in errors:
        print(f"[ERROR] {i['source']}: {i['message']}")


if __name__ == "__main__":
    main()
