[pre-commit](http://pre-commit.com) hook for working with [flux](http://fluxcd.io)


## Usage

```
-   repo: https://github.com/tarioch/flux-check-hook
    rev: v0.2.0
    hooks:
    -   id: check-flux-helm-values
```

The hook depends on the kustomize and helm binaries being available in the path (but it doesn't require to be able to connect to a cluster). The hook will first verify the flux Kustomizations are correct using `kustomize build` and supports repos with multiple clusters and overlays. The second step is to run `helm template` for all changed HelmReleases in the cluster to verify they can be built. The flux Kustomization path can be configured with a `--path` or otherwhise will attempt to find any Kustomziations in the repo.
