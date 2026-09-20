# flux-check-hook

[pre-commit](https://pre-commit.com) hook for working with [flux](https://fluxcd.io).

## Hooks

### `check-flux-helm-values`

Checks the `values` of every flux `HelmRelease` against the chart it installs: the chart is pulled and linted with the
values of the release, so a value that violates the `values.schema.json` of the chart fails the commit before flux
reconciles it.

```yaml
- repo: https://github.com/tarioch/flux-check-hook
  rev: v0.8.0  # use the latest release
  hooks:
    - id: check-flux-helm-values
```

What is checked:

- A `HelmRelease` with `chart.spec` and a `HelmRepository` as the `sourceRef` is pulled from the repository (classic
  `https://` repositories and `oci://` ones) and linted with `helm lint`. The `HelmRepository` has to be defined in a
  yaml file below the directory the hook runs in.
- A `HelmRelease` that only patches another one (no `chart.spec`, for example the `patches` of a kustomize overlay) is
  built with `kubectl kustomize`, starting in the directory of the file and going up. The release of the same name from
  the first kustomization that builds one is checked.
- Charts from other sources (`GitRepository`, `Bucket`) and releases with a `chartRef` (`OCIRepository`) are skipped.

Requirements:

- [`helm`](https://helm.sh) in the `PATH`. It does not need a cluster.
- [`kubectl`](https://kubernetes.io/docs/tasks/tools/) in the `PATH`, only for the releases that are patches.
- Python 3.10 or newer, the hook installs its own environment.

Exclude the files that should not be checked with the usual pre-commit `exclude`:

```yaml
    - id: check-flux-helm-values
      exclude: '^charts/|some-release'
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).
