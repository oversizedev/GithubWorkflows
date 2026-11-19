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
- `.github/actions/xcode-archive` — check the archive configuration against `disallowed-configurations`, compute the build number (`build-number` or `github.run_number + build-number-offset`) and archive with `signing: automatic` (cloud signing through the App Store Connect key), `signing: manual` (the project's own signing settings) or `signing: adhoc` (re-signed by export)
- `.github/actions/xcode-export` — write `ExportOptions.plist` for `method`, `destination`, `signing-style`, `signing-certificate` and `provisioning-profiles`, then run `xcodebuild -exportArchive` (with the App Store Connect key when given)
- `.github/actions/signing-keychain` — import a `.p12` into a temporary keychain on the search list, output the identity and certificate serial
- `.github/actions/asc-provisioning-profile` — keep a provisioning profile with a fixed name active through the App Store Connect API (recreated when it is invalid, expires within 7 days or lacks the certificate) and install it
- `.github/actions/signing-cleanup` — remove the temporary keychain, installed profiles and the `.p8` key (`if: always()`)

## Secrets

| Secret | Used by |
| --- | --- |
| `ACTIONS_TOKEN` | Read access to private `oversizedev` repositories |
| `ASC_KEY_ID`, `ASC_ISSUER_ID` | App Store Connect API key (Admin role, needed for cloud signing) |
| `ASC_PRIVATE_KEY` | Contents of `AuthKey_<id>.p8`: PEM, its base64, or the key body without the `BEGIN`/`END` lines |
| `DEVELOPER_ID_P12`, `DEVELOPER_ID_P12_PASSWORD` | Developer ID Application certificate with its private key as base64 `.p12`, and its password (`build-dmg.yml`) |
| `DISTRIBUTION_P12`, `DISTRIBUTION_P12_PASSWORD` | Apple Distribution certificate with its private key as base64 `.p12`, and its password (`testflight.yml` with `signing: manual`) |

## TestFlight

The signing keychain action bundles the default public Apple intermediates (`AppleWWDRCAG3` and `DeveloperIDG2CA`)
from [Apple PKI](https://www.apple.com/certificateauthority/). It imports these local files so signing does not
depend on resolving `www.apple.com` during every job, and stops when a bundled file does not match its SHA-256 in
`certificates/checksums.sha256`. Custom `intermediates` names still download from Apple. An intermediate that is
already in the keychain is skipped; any other import error stops the job. Signing identities must pass the existing
validity and team checks.

The bundled certificates expire on 2030-02-20 and 2031-09-17, respectively. Refresh them from Apple before
expiration and update their hashes in `certificates/checksums.sha256` and `.github/scripts/test_signing_keychain.py`.
Run that script to verify certificate integrity, expiration, offline default imports, import errors and custom-name
fallback.

`testflight.yml` runs `xcodebuild archive` and `xcodebuild -exportArchive` with `method=app-store-connect` and
`destination=upload`. With `signing: automatic` it signs through the API key (`-allowProvisioningUpdates`) and needs a
development certificate on the runner. With `signing: manual` it imports `DISTRIBUTION_P12`, keeps the
`profile-name` profile active through the API and archives with the project's own signing settings.
`CFBundleVersion` is `github.run_number + build-number-offset`. Uploads share one concurrency group per repository and
scheme and never cancel each other; `upload: false` exports use a separate group per ref, so a pull request export
cannot replace a pending upload from the default branch.

Automatic signing archives with a development certificate, which a runner without an unlocked login keychain does not
have. On such runners use `signing: manual`: the archive configuration of the app target sets
`CODE_SIGN_STYLE: Manual`, `CODE_SIGN_IDENTITY: Apple Distribution` and `PROVISIONING_PROFILE_SPECIFIER: <profile-name>`;
the workflow imports `DISTRIBUTION_P12` into a temporary keychain and keeps the profile `profile-name` for `bundle-id`
active through the API key. Pass `bundle-id` and `profile-name` (and `profile-type: MAC_APP_STORE` for macOS).

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

`build-dmg.yml` archives a macOS app signed ad-hoc (the runner has no development certificate and its keychain
cannot take a new one) and exports it with `method=developer-id` and manual signing. Cloud-managed Developer ID
signing does not work with App Store Connect API keys, so the Developer ID Application identity comes from
`DEVELOPER_ID_P12`: it is imported into a temporary keychain with a random password, added to the user search list
for the job and removed afterwards. The workflow verifies the signature of the app and every nested Mach-O
(Developer ID Application, team, hardened runtime, timestamp, no `get-task-allow`), packs and signs
`<app-name>-<version>-<build>.dmg` with an `Applications` link, notarizes it with `notarytool` using the App Store
Connect key, staples it and checks it with Gatekeeper.

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
      DEVELOPER_ID_P12: ${{ secrets.DEVELOPER_ID_P12 }}
      DEVELOPER_ID_P12_PASSWORD: ${{ secrets.DEVELOPER_ID_P12_PASSWORD }}
```
