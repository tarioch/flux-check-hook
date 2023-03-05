import argparse
import asyncio
import dataclasses
import tempfile
from pathlib import Path

import yaml
from flux_local import git_repo
from flux_local.command import CommandException
from flux_local.helm import Helm

errors: list = []


@dataclasses.dataclass(frozen=True)
class Name:
    namespace: str
    name: str

    def __repr__(self) -> str:
        return f"{self.namespace}/{self.name}"


def main():
    asyncio.run(_asyncMain())
    if len(errors) > 0:
        _printErrors()
        exit(1)


async def _asyncMain():
    parser = argparse.ArgumentParser(
        description="Command line utility for inspecting a local flux repository.",
    )
    parser.add_argument(
        "filename",
        nargs="+",
    )
    parser.add_argument(
        "--path",
        type=Path,
        help="Path to the flux cluster kustomization (optional)",
        required=False,
    )
    args = parser.parse_args()

    release_names = set({})
    for arg in args.filename:
        try:
            if release_name := _getReleaseName(arg):
                release_names.add(release_name)
        except ValueError as err:
            _collectErrors({"source": arg, "message": str(err)})
    try:
        await _validateReleases(release_names, args.path)
    except Exception as err:
        import traceback

        traceback.print_exc()
        _collectErrors({"source": "validateReleases", "message": str(err)})


def _getReleaseName(fileToValidate: str) -> Name | None:
    """Return a namespace and name if valid HelmRelease yaml file."""
    with open(fileToValidate) as f:
        for definition in yaml.load_all(f, Loader=yaml.SafeLoader):
            if not definition or definition.get("kind") != "HelmRelease":
                return None
        if not (metadata := definition.get("metadata")):
            raise ValueError(f"Invalid HelmRelease missing metadata: {definition}")
        if not (name := metadata.get("name")):
            raise ValueError(f"Invalid HelmRelease missing metadata.name: {definition}")
        if not (namespace := metadata.get("namespace")):
            raise ValueError(
                f"Invalid HelmRelease missing metadata.namespace: {definition}"
            )
        return Name(namespace, name)


async def _validateReleases(release_names: set[Name], path: Path | None) -> None:
    manifest = await git_repo.build_manifest(path=path)
    releases = [
        release
        for cluster in manifest.clusters
        for release in cluster.helm_releases
        if Name(release.namespace, release.name) in release_names
    ]
    if not releases:
        _collectErrors(
            {
                "source": "kustomize build",
                "message": f"HelmRelease files not found in Kustomizations: {release_names}",
            }
        )
        return

    # Prune HelmRepository objects to just the active referenced by a HelmRelease
    active_repo_names = {
        Name(release.chart.repo_namespace, release.chart.repo_name)
        for release in releases
    }
    repos = [
        repo
        for cluster in manifest.clusters
        for repo in cluster.helm_repos
        if Name(repo.namespace, repo.name) in active_repo_names
    ]

    with tempfile.TemporaryDirectory() as tmpDir:
        helm = Helm(Path(tmpDir), cache_dir=Path(tmpDir))
        helm.add_repos(repos)

        try:
            await helm.update()
        except CommandException as err:
            _collectErrors({"source": "helm pull", "message": str(err)})
            return

        for release in releases:
            try:
                await helm.template(release)
            except CommandException as err:
                _collectErrors(
                    {
                        "source": "helm template {release.release_name}",
                        "message": str(err),
                    }
                )


def _collectErrors(error: dict[str, str]):
    errors.append(error)


def _printErrors():
    for i in errors:
        print(f"[ERROR] {i['source']}: {i['message']}")


if __name__ == "__main__":
    main()
