import glob
import os.path as path
import subprocess
import sys
import tempfile
from shlex import quote
from string import Template

import yaml

errors: list = list()


def main():
    repos = _buildRepoMap()
    for arg in sys.argv[1:]:
        try:
            _validateFile(arg, repos)
        except Exception as ex:
            _collectErrors(
                {
                    "source": arg,
                    "message": "{0} {1!r}".format(type(ex).__name__, ex.args),
                }
            )
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

            chartSpec = definition["spec"]["chart"]["spec"]

            if chartSpec["sourceRef"]["kind"] != "HelmRepository":
                continue

            chartName = chartSpec["chart"]
            chartVersion = chartSpec["version"]
            chartUrl = repos[chartSpec["sourceRef"]["name"]]
            chartArchive = "{0}-{1}.tgz".format(chartName, chartVersion)

            with tempfile.TemporaryDirectory() as tmpDir:
                with open(path.join(tmpDir, "values.yaml"), "w") as valuesFile:
                    if "spec" in definition and "values" in definition["spec"]:
                        yaml.dump(definition["spec"]["values"], valuesFile)

                res = subprocess.run(
                    f"helm pull --repo {quote(chartUrl)} --version {quote(chartVersion)} {quote(chartName)}",
                    shell=True,
                    cwd=tmpDir,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                if res.returncode != 0:
                    _collectErrors(
                        {
                            "source": chartArchive,
                            "message": res.stdout,
                        }
                    )

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
                        {
                            "source": chartArchive,
                            "message": res.stdout,
                        }
                    )


def _collectErrors(error):
    errors.append(error)


def _printErrors():
    for i in errors:
        print(
            Template("[ERROR] $source: $message").substitute(
                source=i["source"],
                message=i["message"],
            )
        )


if __name__ == "__main__":
    main()
