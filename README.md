[pre-commit](http://pre-commit.com) hook for working with [flux](http://fluxcd.io)


## Usage

```
-   repo: https://github.com/tarioch/pre-commit-flux
    rev: v0.2.0
    hooks:
    -   id: check-flux-helm-values
```

The hook depends on the helm binary being available in the path (but it doesn't require to be able to connect to a cluster).
