# GithubWorkflows

Reusable GitHub Actions workflows and composite actions for Oversize apps and Swift packages.

## Runners

All macOS jobs (checks included) run on `self-hosted, macOS, ARM64, xcode` for private repositories and on `xcode-27` for public ones.
Pass `runs-on` (JSON: `"macos-latest"` or `["self-hosted","macOS"]`) to override. The Linux self-hosted runner
never builds Apple platforms.

## Workflows

| Workflow | Purpose | Key inputs |
| --- | --- | --- |
| `build-app.yml` | Build an Xcode project or workspace without signing | `path` or `workspace`, `scheme`, `destination`, `configuration`, `pre-build`, `xcodegen` |
| `checks.yml` | SwiftFormat and SwiftLint lint plus project commands | `swiftformat`, `swiftlint`, `node-version`, `commands` |
| `build-for-testing.yml` | Build test products once and upload them as an artifact | `path`, `scheme`, `platform`, `artifact`, `pre-build` |
| `test-app.yml` | Run tests from the artifact on the newest simulator, filtered by target | `artifact`, `only-testing`, `skip-testing`, `device-type`, `destination` |
| `testflight.yml` | Archive with cloud signing and upload to TestFlight (or export only) | `path` or `workspace`, `scheme`, `platform`, `configuration`, `team-id`, `build-number`, `build-number-offset`, `upload` |
| `build-swiftpm.yml` | Build a package for one destination | `package`, `destination` |
| `build-swiftpm-all-platforms.yml` | `build-swiftpm.yml` for iOS, macOS, watchOS and tvOS | `package` |
| `test.yml`, `docc.yml`, `bump.yml`, `release.yml`, `swiftformat.yml` | Package tests, docs, versioning, formatting | |

Every Xcode workflow accepts `runs-on`, `xcode-version` and `timeout-minutes`.

## Composite actions

- `.github/actions/xcode-setup` — select Xcode, Homebrew, XcodeGen, fingerprint defaults, private package access
- `.github/actions/xcode-prepare` — generate the project, cache SwiftPM on hosted runners, resolve packages, output `-project`/`-workspace`
- `.github/actions/simulator-destination` — destination for the newest available simulator
- `.github/actions/asc-api-key` — write the App Store Connect `.p8` key to `$RUNNER_TEMP`

## Secrets

| Secret | Used by |
| --- | --- |
| `ACTIONS_TOKEN` | Read access to private `oversizedev` repositories |
| `ASC_KEY_ID`, `ASC_ISSUER_ID` | App Store Connect API key (Admin role, needed for cloud signing) |
| `ASC_PRIVATE_KEY` | Contents of `AuthKey_<id>.p8`, plain or base64 |

## TestFlight

`testflight.yml` runs `xcodebuild archive` and `xcodebuild -exportArchive` with `method=app-store-connect` and
`destination=upload`. Signing is automatic through the API key (`-allowProvisioningUpdates`), so no certificates or
profiles are stored in secrets. `CFBundleVersion` is `github.run_number + build-number-offset`.

```yaml
on:
  push:
    branches: [main]

jobs:
  testflight:
    uses: oversizedev/GithubWorkflows/.github/workflows/testflight.yml@main
    with:
      path: App
      scheme: App (AppStore)
      configuration: AppStore
      team-id: ABCDE12345
      xcodegen: true
      pre-build: xcodegen --spec project.yml
    secrets: inherit
```
