# Release process

## Overview

Tagging `v<X>.<Y>.<Z>` on `main` triggers `.github/workflows/release.yml`, which:

1. Builds the sdist + wheel with `uv build`.
2. Publishes to PyPI via the trusted-publisher OIDC flow (no API token).
3. Creates a GitHub release with the dist artifacts attached.

The companion `milestone-rollover.yml` workflow then closes the just-tagged milestone and opens the next-patch milestone.

## PyPI Trusted Publisher setup (one-time)

Before the first publishing tag push, register this repository as a trusted publisher on PyPI:

1. Sign in at <https://pypi.org/manage/account/publishing/>
2. Choose **Add a new pending publisher** (or **Add publisher** if the project record already exists).
3. Fill in the form **exactly** as follows — these values must match `release.yml` byte-for-byte:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `thousand-eyes-super-mcp` |
   | Owner | `thomaschristory` |
   | Repository name | `thousand-eyes-super-mcp` |
   | Workflow filename | `release.yml` |
   | Environment name | `pypi` |

4. Submit. The trust relationship becomes active immediately.

### Why exact match matters

If you later rename the workflow file or the `environment:` block in `release.yml`, the OIDC token presented at publish time no longer matches the trust record and the publish fails with `invalid-publisher`.

## Tagging a release

1. Land all PRs against the `v<X>.<Y>.<Z>` milestone.
2. Update `CHANGELOG.md` with a dated `[<X>.<Y>.<Z>]` section.
3. Bump `__version__` in `thousand_eyes_mcp/__init__.py` and `version` in `pyproject.toml`.
4. `uv lock && uv sync` to refresh `uv.lock` and the local environment.
5. Commit, push, merge.
6. `git tag v<X>.<Y>.<Z>` on `main` and `git push origin v<X>.<Y>.<Z>`.

Tag push triggers `release.yml` and `milestone-rollover.yml` in parallel.

## Verifying a release

- <https://pypi.org/project/thousand-eyes-super-mcp/> should show the new version within ~1 minute.
- `https://github.com/thomaschristory/thousand-eyes-super-mcp/releases/tag/v<X>.<Y>.<Z>` should have the sdist + wheel attached.
- The milestone for `v<X>.<Y>.<Z>` should be closed and `v<X>.<Y>.<Z+1>` open.

## Troubleshooting

- **`invalid-publisher` from PyPI**: The OIDC subject doesn't match the trust record. Confirm the workflow filename, environment name, and repo owner/name match the PyPI form exactly.
- **Milestone rollover didn't fire**: Confirm the tag matches `v[0-9]+.[0-9]+.[0-9]+`. Pre-release tags (`v0.1.0-rc1`) are intentionally excluded.
- **Tag pushed but release.yml didn't trigger**: Check `.github/workflows/release.yml` filter — it should match `v*`.
