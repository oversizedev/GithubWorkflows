# GithubWorkflows

Reusable GitHub Actions workflows and composite actions for Oversize apps and Swift packages.

## Runners

All macOS jobs (checks included) run on `self-hosted, macOS, ARM64, xcode` for private repositories and on `xcode-27` for public ones.
Pass `runs-on` (JSON: `"macos-latest"` or `["self-hosted","macOS"]`) to override. The Linux self-hosted runner
never builds Apple platforms and is kept for heavy Linux jobs (`swiftformat.yml` builds SwiftFormat from source).
Short Linux jobs (`bump.yml`, `release.yml`, the `release` job of `build-dmg.yml`) always run on GitHub-hosted `ubuntu-latest`: they take a minute and
use Docker actions, which cannot bind-mount the runner's temp folder on the containerized self-hosted runner.

## Workflows

| Workflow | Purpose | Key inputs |
| --- | --- | --- |
| `build-app.yml` | Build an Xcode project or workspace without signing | `path` or `workspace`, `scheme`, `destination`, `configuration`, `pre-build`, `xcodegen` |
| `checks.yml` | SwiftFormat and SwiftLint lint plus project commands | `swiftformat`, `swiftlint`, `node-version`, `commands` |
| `build-for-testing.yml` | Build test products once and upload them as an artifact; macOS builds are signed ad-hoc without hardened runtime, so no certificate is needed | `path`, `scheme`, `platform`, `artifact`, `pre-build` |
| `test-app.yml` | Run tests from the artifact on the newest simulator, filtered by target | `artifact`, `only-testing`, `skip-testing`, `device-type`, `destination` |
| `testflight.yml` | Archive with cloud signing and upload to TestFlight (or export only) | `path` or `workspace`, `scheme`, `platform`, `configuration`, `team-id`, `build-number`, `build-number-offset`, `upload` |
| `build-dmg.yml` | Archive a macOS app with Developer ID cloud signing, pack a DMG, notarize, staple, optionally attach to a GitHub Release | `path` or `workspace`, `scheme`, `configuration`, `team-id`, `app-name`, `marketing-version`, `notarize`, `release-tag` |
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

## DMG

`build-dmg.yml` archives a macOS app and exports it with `method=developer-id`. The Developer ID certificate is
cloud-managed: the App Store Connect Team key with the Admin role signs through `-allowProvisioningUpdates`, so no
`.p12` is stored in secrets. The workflow verifies the signature (Developer ID Application, team, hardened runtime,
timestamp, no `get-task-allow`), packs `<app-name>-<version>-<build>.dmg` with an `Applications` link, notarizes it
with `notarytool` using the same key, staples it and checks it with Gatekeeper. The DMG itself is not signed: cloud
signing has no local private key, and a notarized, stapled UDIF image with a signed app passes Gatekeeper.

The DMG is uploaded as an artifact. With `release-tag` a separate `ubuntu-latest` job attaches it to the GitHub
Release of that tag (created when missing); the caller job must grant `contents: write`, and `notarize: false`
cannot be combined with a release.

```yaml
on:
  push:
    tags: ["*.*.*"]

jobs:
  dmg:
    permissions:
      contents: write
    uses: oversizedev/GithubWorkflows/.github/workflows/build-dmg.yml@main
    with:
      path: App
      scheme: App (AppStore)
      configuration: AppStore
      team-id: ABCDE12345
      app-name: App
      marketing-version: ${{ github.ref_name }}
      release-tag: ${{ github.ref_name }}
    secrets:
      ASC_KEY_ID: ${{ secrets.ASC_KEY_ID }}
      ASC_ISSUER_ID: ${{ secrets.ASC_ISSUER_ID }}
      ASC_PRIVATE_KEY: ${{ secrets.ASC_PRIVATE_KEY }}
```
