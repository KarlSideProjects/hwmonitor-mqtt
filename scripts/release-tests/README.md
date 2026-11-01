# Compiled Linux release test support

`docker_acceptance.py` runs the supplied official bootstrap and compiled archive in a disposable nested Docker daemon. The caller must supply exact candidate paths, archive digest, source commit, version, platform, and a new output directory. The previous archive must have a different digest, source commit, and version. The host architecture must match the platform; run ARM64 on an ARM64 host or a full ARM64 VM. Docker, network access for image and package downloads, and permission to run a privileged nested daemon are required.

```sh
python3 scripts/release-tests/docker_acceptance.py \
  --bootstrap /absolute/path/bootstrap.sh \
  --archive /absolute/path/sender-linux-x64.tar.gz \
  --sha256 ARCHIVE_SHA256 --source SOURCE_COMMIT_SHA \
  --old-archive /absolute/path/old-sender-linux-x64.tar.gz \
  --old-sha256 OLD_ARCHIVE_SHA256 --old-source OLD_SOURCE_COMMIT_SHA \
  --old-version 0.0.1 \
  --version 1.2.3 --platform linux-x64 --output /absolute/new/output-directory
```

The runner starts an isolated daemon without a bridge or iptables changes and an anonymous broker inside that daemon's enclosing network namespace. The sender override removes the host device mounts and additional capabilities. It checks the compiled version, one running sender, fresh, non-retained full and ESP MQTT messages, settings preservation during an update, and running and stopped-state rollback after a real one-shot process start failure. It records `run.log` and available MQTT and process logs in the output directory. The runner removes its named outer container and installer image and checks their absence; any nonzero exit is a failed acceptance, including a rollback failure.

The override excludes physical devices, so this test does not validate physical SMART readings. A pass does not establish PVE, systemd, ARM64, Windows, or release approval. ARM64 requires a separate actual ARM64 execution. PVE requires the separate fresh-guest acceptance below.

`Dockerfile.installer-double` supplies the existing isolated fault-injection integration tests. Those tests substitute Docker commands and are not real Docker acceptance evidence. The loopback endpoint is test-only and serves an archive only after matching its digest and manifest identity.

### Disposable PVE guest

`pve_acceptance.py` provisions a fresh x64 Debian cloud image in an isolated Docker/QEMU guest. Supply an official Debian 13 generic-amd64 cloud image and its independently obtained SHA-512, as well as both immutable releases and the official bootstrap SHA-256. The runner rejects mismatched inputs, generates a private SSH key and disks, installs PVE using the pinned Proxmox repository key and signed APT metadata, and requires a PVE-kernel reboot before exercising the real compiled bootstrap. Docker, KVM or QEMU TCG, network access, SSH, and sufficient space for a 20 GiB sparse system disk are required. Keep the new output directory private: it contains guest disks, credentials, and logs.

```sh
python3 scripts/release-tests/pve_acceptance.py \
  --bootstrap /absolute/bootstrap.sh --bootstrap-sha256 BOOTSTRAP_SHA256 \
  --archive /absolute/new.tar.gz --sha256 NEW_SHA256 --source NEW_COMMIT --version NEW_VERSION \
  --old-archive /absolute/old.tar.gz --old-sha256 OLD_SHA256 --old-source OLD_COMMIT --old-version OLD_VERSION \
  --cloud-image /absolute/debian-13-generic-amd64.qcow2 --cloud-image-sha512 IMAGE_SHA512 \
  --output /absolute/new/private-directory
```

A zero exit requires installation, rollback from active and stopped service states, upgrade with preserved settings, TLS-verified PVE API access, and live non-retained MQTT assertions. Cleanup removes the owned container and image tag and checks its loopback port; inspect the private log after any failure. The NVMe is virtual and may report critical health; this test does not establish physical-disk health or release approval. The APT package versions are selected from signed repository metadata at run time and are not reproducible pins; the image digest, artifact identities, and guest logs bind each run's inputs and results.

One official dated input used for the WIP run is [Debian 13 generic amd64 20261026-2643](https://cloud.debian.org/images/cloud/trixie/20261026-2643/debian-13-generic-amd64-20261026-2643.qcow2), whose [published SHA-512 list](https://cloud.debian.org/images/cloud/trixie/20261026-2643/SHA512SUMS) records `5bd891c6e62d01639f52a5587b72a4607421e445732214be787dba6043cf9797a6ff90bed982aada6a6c7e619f1f956d961c4fad76757171abd607920aac098a`. The runner verifies the supplied image bytes itself. Guest reset is restricted by a per-run cloud-init owner marker, the disposable hostname, and virtual-machine detection.
