# Sender release orchestration

Master pushes run `sender-release.yml`; pull requests and manual source checks run `ci.yml`. The release workflow calls that same check workflow, preserving Python 3.11/3.13, native systemd/MQTT, and Windows PowerShell 5.1/7 and LHM checks. Feature-branch pushes do not run a second copy of PR checks. Linux x64/ARM64 Docker acceptance and Windows compiled acceptance remain platform build gates; PVE VM acceptance is a separate harness.

Versions remain `0.1.<ordinal>` from complete master first-parent history. Linux embeds the selected version in its entrypoint; Windows uses its validated adjacent manifest identity. Both full and ESP MQTT payloads add top-level `sender_version` (SemVer string, or JSON null for unknown/source runs); existing fields remain unchanged. No runtime lookup of the latest release supplies identity.

## Read-only preflight

Run `python3 scripts/release_controller.py preflight --master <full-master-sha> --source <full-source-sha>`. This validates source/version identity and reports readiness to run checks and builds separately from the publisher credential prerequisite. It does not execute tests, compile, contact GitHub, write releases, or print secret values. `--require-publish` returns failure when `SENDER_RELEASE_TOKEN` is missing; ordinary preflight succeeds with a blocked-publication report.

The release workflow runs this prerequisite in `sender-release-publisher` before either expensive platform build. The release workflow gates source checks on this prerequisite too; an explicit manual `build_only` run skips the credential preflight. A present credential is reported as `credential-present-unverified`: presence cannot prove repository scope, expiry, Contents write permission, environment policy, or successful publication. Preparation and drain retain their existing remote validation and failure handling. Tests and build jobs receive no publisher credential.

## Remaining publisher setup

Read-only inspection on 2026-11-01 found no environment secrets in `sender-release-publisher`, no environment protection rules or deployment branch policy, and no repository activation variable. No credential or environment configuration was changed. The agent cannot create a user's new fine-grained PAT through the existing repository API credentials; the following human configuration is required:

1. Create a new expiring fine-grained PAT with resource owner `KarlSideProjects`, selected repository **only** `hwmonitor-mqtt-releases`, and repository **Contents: read and write** (Metadata read is implicit). Complete any required owner approval. Do not reuse the owner's broad token or the source repository `GITHUB_TOKEN`.
2. In the source repository's `sender-release-publisher` environment, restrict deployment branches to `master` and choose required reviewers according to the release policy. Store the new PAT as environment secret `SENDER_RELEASE_TOKEN`. The interactive command `gh secret set SENDER_RELEASE_TOKEN --repo KarlSideProjects/hwmonitor-mqtt --env sender-release-publisher` accepts it without putting the value in shell history.
3. Run the trusted master release workflow. Review source checks, all platform acceptance, inspection and remote publication results. Credential presence alone does not certify release readiness.
4. To enable bounded scheduled recovery, configure source repository variable `SENDER_RELEASE_ACTIVATION_SHA` to the reviewed first-parent master boundary. Do not select older commits blindly: each missing source can initiate all platform builds. Leave this unset until activation is intended.

## Controller and recovery

`release_controller.py prepare` validates exact assets, checksums, committed installer bytes, source/version and all platform evidence before constructing a receipt for `release_backend.py`. Evidence remains private: each platform directory contains `record.json`, `report.xml`, `inventory.json`, `runtime/`, and Linux `image.tar`. Source-specific preparation concurrency protects STAGING/READY draft creation; a globally serialized drain verifies remote assets, publishes READY drafts and reconciles latest without moving it backwards. Published assets are immutable; identity collisions, missing digests and rewritten history fail closed.

Scheduled or manual drains recover READY drafts after interruption. `pending --master <sha> --start <sha> --limit <1-100>` lists missing/STAGING first-parent sources; `recover` dispatches bounded retries using only the recovery job's source `github.token` with `actions: write`. Recovery prefers unattempted then least recently attempted sources (history order breaks ties); its newest 10,000 run history limit can classify older attempts as unknown. READY drafts survive Actions queue limits. Investigate inconsistent releases rather than deleting published assets or manually advancing latest.

The loopback-only staging prototype is a test interface. Source tests and a preflight report do not establish fresh compiled acceptance, legal approval, or flow delivery approval; those remain candidate-specific checks.
