# Contributing

`flux-check-hook` is a [pre-commit](https://pre-commit.com) hook, `check-flux-helm-values`, that lints the values of flux
`HelmRelease`s against their chart, see the [README](README.md). It is installed by pre-commit from a git tag, it is not
published to PyPI.

## Layout

| Path | Content |
|---|---|
| `.pre-commit-hooks.yaml` | the hook definition that pre-commit reads, the `entry` is the console script of the package |
| `src/pre_commit_flux/check_flux_helm_values.py` | the hook |
| `tests/test_precommit.py` | the tests, they run the hook with the real `helm` and `kubectl` against a chart repository served from localhost, and skip themselves without the tools |
| `tests/test_run.py` | the tests of how the commands are run (timeout, missing command), they need neither `helm` nor `kubectl` |
| `tests/fixtures/chart/` | the chart the tests serve, with a `values.schema.json` |
| `tests/fixtures/flux/` | the flux resources the tests run the hook on, one directory per case (`kustomization/` patches the release in `default/`) |

## Setup

```bash
uv sync --locked --dev
```

Python 3.10 to 3.14 are supported and tested. The tests need [`helm`](https://helm.sh) and `kubectl` in the `PATH`, they need no
network and no cluster.

## Checks

CI runs the same commands, all of them have to pass:

```bash
uvx pre-commit run --all-files   # ruff, ruff format, mypy, uv-lock, zizmor, hook manifest
uv run pytest
```

Things that catch people out:

- `pre-commit run --all-files` only looks at files tracked by git. `git add` new files before running it, otherwise
  they are not checked (and CI then fails on them).
- mypy runs in the project environment (a local pre-commit hook calling `uv run mypy`), so it checks against the types of
  the installed packages. Stub packages (`types-*`) belong into the `dev` dependency group.
- Whether the hook can be installed from the repository is a check of its own in CI (`uvx pre-commit try-repo . check-flux-helm-values
  --files tests/fixtures/chart/Chart.yaml`), run it after changing `.pre-commit-hooks.yaml`, `pyproject.toml` or the
  entry point.

## Code

- The hook prints `[ERROR] <source>: <message>` for every problem and exits with 1, the messages are what its users see
  in the commit output.
- `helm` and `kubectl` are called without a shell, every call goes through `_run`. It stops a command after 300 seconds
  and reports a command that is not installed like a failing one.
- `ruff format` decides the formatting, the lint rules are pinned in `pyproject.toml` because the ruff defaults change
  between versions.
- `pyproject.toml` only holds what pre-commit needs to build and install the hook. The version is a placeholder, the
  version of a release is its git tag.

## Dependencies

- `uv.lock` is committed. Regenerate it with the uv version of the `uv-lock` pre-commit hook (`rev` in
  `.pre-commit-config.yaml`), a different uv version rewrites unrelated parts of the file:
  `uvx --from uv==<rev> uv lock`.
- Dependabot (`.github/dependabot.yml`) opens grouped PRs for minor and patch updates of Python packages weekly and for
  GitHub Actions monthly. Major updates come as separate PRs. New releases wait 7 days (cooldown), security updates do not.

## Git and pull requests

- Branch off `main`, named `feature/…`, `bugfix/…` or `chore/…` (snake_case after the prefix). The prefix labels the PR
  (`.github/pr-labeler.yml`), and the label decides the category in the release notes (the shared
  `release-drafter.yml` of [tarioch/.github](https://github.com/tarioch/.github)).
- Commit subjects are imperative and start with a capital letter ("Match the patched release by name"), the body
  explains why.
- Changes go through pull requests into `main`.

## CI and releases

`.github/workflows/ci.yml` runs `lint`, `test` (matrix) and `hook` for every pull request and for pushes to `main`.
Workflows use the least permissions they need, and every action is pinned to a commit SHA (Dependabot keeps the pins
current, zizmor fails for unpinned actions or broad permissions).

- Release notes are drafted by release-drafter. Publishing the draft creates the tag `vX.Y.Z`, that tag is what users put
  into `rev:` of their `.pre-commit-config.yaml`. Nothing is uploaded anywhere.
- The `test` job checks that `helm` and `kubectl` exist on the runner instead of letting the tests skip.
