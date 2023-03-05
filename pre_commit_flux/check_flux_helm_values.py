import argparse
import asyncio
import dataclasses
import logging
import tempfile
from pathlib import Path
from typing import Generator

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
    parser = argparse.ArgumentParser()
    parser.add_argument("filename", nargs="+")
    parser.add_argument(
        "--path",
        type=Path,
        help="Path to the flux cluster kustomizations",
        required=False,
    )
    parser.add_argument("-v", "--verbose", type=bool, action=argparse.BooleanOptionalAction)
    asyncio.run(_asyncMain(parser.parse_args()))


async def _asyncMain(args):
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    release_names = set({})
    for arg in args.filename:
        try:
            release_names |= set(_getReleaseNames(arg))
        except ValueError as err:
            _collectErrors({"source": arg, "message": str(err)})
    try:
        await _validateReleases(release_names, args.path)
    except Exception as err:
        _collectErrors({"source": "validateReleases", "message": str(err)})

    if len(errors) > 0:
        _printErrors()
        exit(1)


def _getReleaseNames(fileToValidate: str) -> Generator[Name, None, None]:
    with open(fileToValidate) as f:
        for definition in yaml.load_all(f, Loader=yaml.SafeLoader):
            if (
                not definition
                or "kind" not in definition
                or definition["kind"] != "HelmRelease"
            ):
                continue
            if (
                not (metadata := definition.get("metadata"))
                or not (name := metadata.get("name"))
                or not (namespace := metadata.get("namespace"))
            ):
                raise ValueError(f"HelmRelease missing metadata fields: {definition}")
            yield Name(namespace, name)


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

    # Build a list of HelmRepositories referenced by chagned HelmReleases
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
                cmd = await helm.template(release)
                await cmd.run()
            except CommandException as err:
                _collectErrors({"source": "helm template", "message": str(err)})


def _collectErrors(error: dict[str, str]):
    errors.append(error)


def _printErrors():
    for i in errors:
        print(f"[ERROR] {i['source']}: {i['message']}")


if __name__ == "__main__":
    main()
