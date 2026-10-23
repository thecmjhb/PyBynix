# PyPI publishing

PyBynix is distributed as the `pybynix` package. After a release is published, users can
install the public package from PyPI:

```console
python -m pip install pybynix
```

Optional integrations are available as one free package with extras:

```console
python -m pip install "pybynix[browser,network,dataframe]"
python -m playwright install chromium
```

The package name and the import/CLI names are intentionally different in only the usual
Python way:

| Purpose | Name |
| --- | --- |
| PyPI package | `pybynix` |
| Python import | `pybynix` |
| CLI command | `pybynix` |
| GitHub repository | `thecmjhb/PyBynix` |

## One-time PyPI setup

Use PyPI Trusted Publishing instead of storing a long-lived PyPI API token in GitHub:

1. Sign in to PyPI.
2. Open **Publishing** or **Your projects → Publishing**.
3. Add a pending trusted publisher with:
   - Owner: `thecmjhb`
   - Repository: `PyBynix`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
4. Save the publisher configuration.

The pending configuration can be created before the first release of `pybynix`. The workflow
uses GitHub's short-lived OIDC identity and publishes only when a maintainer pushes a version
tag beginning with `v`.

## Release procedure

Run these commands from the public repository checkout after the GitHub Actions checks have
passed:

```console
git checkout main
git pull --ff-only origin main
git status --short --branch
git tag -a v1.0.0 -m "Release PyBynix 1.0.0"
git push origin v1.0.0
```

The `v1.0.0` tag starts `.github/workflows/publish.yml`. It builds the wheel and source
distribution from the tagged commit and publishes those two PyPI distributions. The repository
source ZIP is not uploaded to PyPI.

Verify the release before telling users to install it:

```console
python -m pip index versions pybynix
python -m pip install --upgrade pybynix
python -c "import pybynix; print(pybynix.__file__)"
pybynix --help
```

## Version rule

Keep the version in `pyproject.toml` and the Git tag aligned. For example:

| `pyproject.toml` | Git tag |
| --- | --- |
| `1.0.0` | `v1.0.0` |
| `1.0.1` | `v1.0.1` |

PyPI does not allow re-uploading the same version. For any content change after `1.0.0`, bump
the version, update the changelog, run the checks, and create a new tag.

## Local build check without publishing

These commands build distributions locally but do not upload anything:

```console
python -m pip install --upgrade build
python -m build
python -m pip install --force-reinstall dist/pybynix-1.0.0-py3-none-any.whl
```

Do not run `twine upload` unless Trusted Publishing is unavailable and the owner has explicitly
approved an alternative PyPI credential flow.
