# Installer workflow improvements implementation plan

> For agentic workers: execute the independent file-owned tasks below, then perform one integrated review.

**Goal:** Implement the seven user-approved retrospective recommendations in one PR.

**Architecture:** Reuse the existing flow checker, pytest/PowerShell CI and isolated
acceptance guests. Keep feature-local review inputs local. Add bounded Actions
observations and small acceptance receipts. GitHub enforces the resulting checks.

**Tech stack:** Python standard library, pytest, PowerShell, GitHub Actions and QEMU.

**Spec:** The user approved all seven recommendations in the current retrospective.
This plan records their execution, including repository-local review policy in place
of changing an unrelated global skill installation.

## Constraints and review focus

- Preserve the live sender, user settings, credentials and independent review.
- Keep failed observation handles; a timeout is pending, not a terminal result.
- A partial acceptance run cannot produce a passing receipt.
- Heavy guests run on hosted CI and are isolated from existing containers.
- Migration of flow policy receives independent scrutiny in this same PR.

## Tasks

- [ ] Scope manifest policy inputs and test unrelated fingerprint stability.
- [ ] Execute source Windows tests under both supported PowerShell versions.
- [ ] Reuse VM provisioning for source install, reinstall, settings and MQTT acceptance.
- [ ] Schedule heavy acceptance, upload sanitized receipts and clean owned resources.
- [ ] Add bounded Actions observations with deterministic tests and private diagnostics.
- [ ] Update AGENTS navigation and adopt one per-PR review allowance.
- [ ] Commit required-check configuration, enable GitHub protection and verify it.
- [ ] Synchronize flows, review exact candidate, run checks, open one PR and merge.
