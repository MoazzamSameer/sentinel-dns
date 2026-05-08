# Releasing

How `sentinel-dns` gets to PyPI. Mostly automated; the one manual step is the version bump + git tag.

## Workflow overview

[`.github/workflows/release.yml`](../.github/workflows/release.yml) has two jobs:

| Job | Triggers | What it does |
|---|---|---|
| **build** | every PR touching `pyproject.toml` / `sentinel_dns/**` / the workflow itself, plus tag pushes | `python -m build`, smoke-test the wheel in a clean venv (`pip install dist/*.whl && sentinel-dns --help`), upload the dist as a workflow artifact |
| **publish** | tag push matching `v*` only | downloads the dist artifact, publishes to PyPI via OIDC trusted publishing |

Pull-requests get the build half so packaging regressions are caught before merge. Only tag pushes publish.

## One-time PyPI setup (already done? skip)

Before the first release ever runs, the project owner has to register the GitHub repo as a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/):

1. Reserve the project name on PyPI: log in, create a project named `sentinel-dns` (it's fine if there's no release yet — pending publishers can be configured for unclaimed names too).
2. Project settings → **Publishing** → **Add a new pending publisher** with:
   - Owner: `MoazzamSameer`
   - Repository name: `sentinel-dns`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
3. In the GitHub repo settings → **Environments** → create an environment named `pypi`. Add no secrets — OIDC handles auth.

After that, any tag push that matches `v*` publishes without storing a token anywhere.

## Cutting a release

```bash
# 1. Bump version in pyproject.toml. Pre-1.0 conventions:
#    - 0.1.0a1, 0.1.0a2, ...    alphas
#    - 0.1.0b1, 0.1.0b2, ...    betas
#    - 0.1.0rc1, ...            release candidates
#    - 0.1.0                    actual release

$EDITOR pyproject.toml          # version = "0.1.0a1"

# 2. Commit the bump and push it.
git commit -am "chore: bump version to 0.1.0a1"
git push

# 3. Tag it. The tag name is what GitHub Actions watches for.
git tag -a v0.1.0a1 -m "v0.1.0a1"
git push origin v0.1.0a1

# 4. Watch the workflow run.
gh run watch
```

After a successful publish, `pip install sentinel-dns==0.1.0a1` works.

## Local pre-release verification

Reproduces what the workflow does, in case you want to catch issues before pushing the tag:

```bash
python -m pip install --upgrade build
python -m build

# Inspect the wheel — make sure all the modules are there
unzip -l dist/sentinel_dns-*.whl | head -30

# Smoke-test in a clean venv
python -m venv /tmp/dist-test
/tmp/dist-test/bin/pip install dist/sentinel_dns-*.whl
/tmp/dist-test/bin/sentinel-dns --help
/tmp/dist-test/bin/sentinel-dns \
    --listen-port 5354 \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce &

dig @127.0.0.1 -p 5354 google.com +short
# → 142.251.223.142

dig @127.0.0.1 -p 5354 1ce6-route.fixionmunici9al.lat
# → ;; status: NXDOMAIN

kill %1
```

The smoke test is what the workflow runs on every PR — running it locally first means you don't have to wait on CI.

## Versioning policy

Pre-1.0:

- Anything that changes the wire protocol or breaks deployed configs gets a minor bump (`0.1.0` → `0.2.0`).
- Bug fixes and additive features bump the patch (`0.1.0` → `0.1.1`).
- We don't pretend to honor semver in the strong sense yet.

Once we cut a real `1.0.0`:

- Major bumps for breaking changes only.
- Minor for additive features.
- Patch for fixes.

The `1.0.0` gate per the roadmap is: actual Pi 4 hardware verification + at least one non-author user reporting a real catch. Until then, all releases are `0.x` previews.

## Caveats

1. **First release is gated on more than just this workflow.** Per [`ROADMAP.md`](ROADMAP.md), the v0.1 release announcement waits on Pi 4 hardware verification + user feedback signals. The workflow is ready; pulling the trigger isn't.
2. **The `pypi` environment is the privilege boundary.** Configuring an environment in GitHub lets you require approval for the `publish` job — useful if you want a maintainer to OK each release after the build is green. Currently no protection rule is set; first push of a tag publishes immediately. Add a required-reviewer rule on the environment if needed.
3. **No code-signing / SLSA provenance attestations yet.** OIDC trusted publishing is the modern replacement for API tokens; it doesn't give you sigstore signatures by default. The `pypa/gh-action-pypi-publish@release/v1` action does support `--attestations` for SLSA — could turn it on later if there's demand.
4. **No matrix testing across Python versions.** The build job runs on Python 3.13 only. Adding a 3.11/3.12/3.13 matrix is one line of YAML; deferred until someone hits a version-specific issue.
5. **No automated changelog.** Tag pushes don't generate release notes from commits. GitHub Releases entry has to be created by hand (or via `gh release create`). Could automate with `release-please` later.
6. **Wheel is pure-Python, single-arch.** Every Python dependency we have (dnspython, scikit-learn, numpy, joblib, httpx) ships its own platform wheels — we don't need to. If the future async scorer adds a C extension, we'd need cibuildwheel or similar.