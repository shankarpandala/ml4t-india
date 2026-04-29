# Releasing

This guide covers how to cut a new release of `ml4t-india`, publish it to
PyPI, and keep conda-forge in sync.

## Overview

| Step | What happens | Who / what does it |
|---|---|---|
| `git tag v0.1.0 && git push origin main --tags` | Triggers the publish workflow | Developer |
| `publish.yml` runs | Builds wheel + sdist, uploads to PyPI via OIDC | GitHub Actions |
| `regro-cf-autotick-bot` detects new PyPI release | Opens a PR to conda-forge | Bot (automatic after first release) |

No API tokens are stored in GitHub Secrets. PyPI upload uses OIDC Trusted
Publishers — GitHub's identity token is exchanged for a short-lived PyPI
upload credential at runtime.

---

## Versioning

Versions are derived from git tags by `hatch-vcs`. The tag format must be
`vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`). No manual version bumps in source files.

```bash
git tag v0.1.0          # create the tag locally
git push origin main    # push commits
git push origin v0.1.0  # push the tag (triggers publish.yml)
```

Or in a single command:

```bash
git push origin main --tags
```

Tags pushed to `main` that match `v*` trigger the publish workflow. Tags on
other branches are ignored by the workflow filter.

---

## PyPI — OIDC Trusted Publishers

### One-time setup (done once per PyPI project)

1. Create the project on PyPI (first upload only, see below).
2. Go to **PyPI > Your projects > ml4t-india > Settings > Publishing**.
3. Click **Add a new publisher** and fill in:

   | Field | Value |
   |---|---|
   | Owner | `shankarpandala` (your GitHub username) |
   | Repository | `ml4t-india` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` (must match the `environment:` in the workflow) |

4. Save. PyPI now trusts GitHub Actions running `publish.yml` in the
   `pypi` environment on this repository to upload to `ml4t-india`.

No API token is ever created or stored. The trust relationship is purely
identity-based (GitHub's OIDC JWT).

### First upload

Before the Trusted Publisher relationship exists on PyPI, the project record
itself must be created. The easiest way is to upload manually once:

```bash
# Build locally
hatch build

# Upload with a traditional API token (one-time only)
pip install twine
twine upload dist/*
```

PyPI will prompt for your username and password (or use an API token created
in your PyPI account settings). After this single upload, configure the
Trusted Publisher above and delete the API token.

All subsequent releases go through GitHub Actions with no stored secrets.

### Automated publish workflow

`.github/workflows/publish.yml` triggers on `push` to tags matching `v*`:

```
git tag v0.1.0
git push origin main --tags
```

The workflow:
1. Checks out the tagged commit.
2. Runs `hatch build` to produce `dist/*.whl` and `dist/*.tar.gz`.
3. Calls `pypa/gh-action-pypi-publish@release/v1` which exchanges the GitHub
   OIDC token for a short-lived PyPI upload token — no stored secrets needed.

The `environment: pypi` declaration in the workflow gates the `id-token: write`
permission to the PyPI environment, which can have additional protection rules
(required reviewers, deployment branch restrictions) in your GitHub repository
settings.

---

## conda-forge

### First release — manual PR to staged-recipes

conda-forge does not auto-discover new packages. The first release requires a
manual PR to [conda-forge/staged-recipes](https://github.com/conda-forge/staged-recipes):

1. Fork `conda-forge/staged-recipes`.
2. Copy `conda-recipe/meta.yaml` from this repository into
   `staged-recipes/recipes/ml4t-india/meta.yaml`.
3. Update the `version` and `sha256` fields for the release tarball:

   ```bash
   # Get the sha256 of the PyPI sdist
   pip download --no-deps --no-binary :all: ml4t-india==0.1.0
   sha256sum ml4t_india-0.1.0.tar.gz
   ```

4. Open a PR against `conda-forge/staged-recipes:main`. The conda-forge CI
   will lint and build your recipe. Address any review comments.
5. Once merged, conda-forge creates a `ml4t-india-feedstock` repository and
   the package becomes available on the `conda-forge` channel.

> **Prerequisite check:** All dependencies listed in `meta.yaml`
> (`requirements/run`) must already be on conda-forge. If `ml4t` (the core
> backtest library) is not yet published there, the recipe will fail to build.
> Publish upstream dependencies first.

### Subsequent releases — automatic

After the feedstock exists, `regro-cf-autotick-bot` detects new PyPI releases
and opens a PR on the feedstock to update `version` and `sha256`. Review and
merge that PR to publish the new version to conda-forge. No manual recipe
editing is needed.

### The recipe file

`conda-recipe/meta.yaml` in this repository is the template recipe. It is
**not** used directly by conda-forge CI after the feedstock is created —
conda-forge maintains its own copy in the feedstock repository. Keep
`conda-recipe/meta.yaml` updated here as a reference for the staged-recipes
PR and for local recipe testing with `conda-build`.

### Test the recipe locally

```bash
conda install conda-build
conda build conda-recipe/ --no-anaconda-upload
```

This builds the package in an isolated conda environment and runs the
`test.imports` block from `meta.yaml`. Fix any build errors before opening
the staged-recipes PR.

---

## Release checklist

```
[ ] All tests pass on main: pytest -ra
[ ] CHANGELOG.md updated with release notes under the new version heading
[ ] hatch version check: hatch version  (should match the tag you will push)
[ ] git tag v0.1.0
[ ] git push origin main --tags
[ ] Watch publish.yml on GitHub Actions — verify PyPI upload succeeds
[ ] Confirm new version visible at https://pypi.org/project/ml4t-india/
[ ] (First release only) Open staged-recipes PR for conda-forge
[ ] (Subsequent releases) Merge regro-cf-autotick-bot PR on feedstock
```

---

## Troubleshooting

### `publish.yml` fails with "403 Forbidden" on PyPI upload

The Trusted Publisher is not configured, or the `environment` name in the
workflow (`pypi`) does not match what was entered on PyPI. Check:
1. The publisher entry on PyPI lists the correct owner, repository, workflow
   file name, and environment.
2. The GitHub Actions environment named `pypi` exists in your repository
   settings and the branch protection rules allow the workflow to run on tag
   pushes.

### `hatch version` returns a dev version

The tag was not pushed, or the working tree has uncommitted changes. Ensure
`git describe --tags --exact-match HEAD` returns the tag cleanly before pushing.

### conda-build fails with missing dependency

A `requirements/run` entry is not available on conda-forge. Either add a
`pip` fallback in the `meta.yaml` or publish the missing package to
conda-forge first.
