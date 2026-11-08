# Delivery tooling baseline

The baseline is the committed source at the start of this maintenance change.
`Makefile` runs `make check`; `.github/workflows/ci.yml` validates flow evidence,
Python tests, native systemd installation and selected Windows tests. Repository
GitHub rulesets and branch protection were absent when inspected.

There is no repository GitHub-run watcher. The installed `agent-ops wait` watches
an Orca terminal and does not observe GitHub Actions. `gh run watch` can observe
Actions, but this repository does not retain a bounded observation state or a
private failure-log receipt. A new `development.delivery` flow owns that behavior.

The existing compiled acceptance harnesses under `scripts/release-tests/` create
isolated Docker/QEMU guests. They do not test the source one-line installer.
Source acceptance will reuse their provisioning boundaries and record its own
candidate identity, MQTT observations and cleanup result.
