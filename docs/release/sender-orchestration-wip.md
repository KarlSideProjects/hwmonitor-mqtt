# Sender release orchestration WIP

The release workflow is deliberately failing: it derives a source version but does not build or publish a release. Do not enable publication from this branch. The loopback draft staging script is a test prototype, not a GitHub publisher; it accepts only `http://127.0.0.1` and has no credential support.

A complete implementation must validate all three platform artifacts, checksums, installer scripts and independently approved legal inputs for one committed source before a draft becomes ready. The inspector under development consumes an external build record, build report, inventory, runtime directory and archive; Linux x64 also requires an image archive. Its approved notice and source fingerprints are currently empty. A passing sample inspection is not legal approval.

Versions use `0.1.<ordinal>` for a commit in the complete master first-parent history. The publisher must compare every existing release's source and tag before assigning a version; a rewritten history or collision must stop for investigation. Source-exclusive preparation is required because GitHub can allow duplicate draft tags. The current prototype does not provide this lock.

A future publisher must retain validated ready drafts before entering the serialized drain, verify remote assets and receipts, and reconcile publication and latest promotion after interruption. It must not mutate ready or published assets or move latest backwards. Scheduled and manual drains need to recover queued candidates; Actions concurrency alone is not durable storage.

Only trusted master pushes and default-ref maintenance may reach a publisher. The source repository token cannot publish to the separate public repository. Before activation, a human must provision a fine-grained credential restricted to `KarlSideProjects/hwmonitor-mqtt-releases`, with only the target release write permission needed, and make it available solely to the trusted publisher job. Build and test jobs must not receive that credential. Do not copy an existing broad credential.

Before a non-draft delivery, complete the project flow synchronization, independent review and trusted-base validation, actual platform execution and installation tests, third-party obligations and human approvals. No production release, tag, draft or secret was created by this WIP.
