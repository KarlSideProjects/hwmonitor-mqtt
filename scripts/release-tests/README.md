# Compiled Linux release test support

`docker_acceptance.py` runs the supplied official bootstrap and compiled archive in a disposable nested Docker daemon. The caller must supply exact candidate paths, archive digest, source commit, version, platform, and a new output directory. The host architecture must match the platform; run ARM64 on an ARM64 host or a full ARM64 VM. Docker, network access for image and package downloads, and permission to run a privileged nested daemon are required.

```sh
python3 scripts/release-tests/docker_acceptance.py \
  --bootstrap /absolute/path/bootstrap.sh \
  --archive /absolute/path/sender-linux-x64.tar.gz \
  --sha256 ARCHIVE_SHA256 --source SOURCE_COMMIT_SHA \
  --version 1.2.3 --platform linux-x64 --output /absolute/new/output-directory
```

The runner starts an isolated daemon without a bridge or iptables changes and an anonymous broker inside that daemon's enclosing network namespace. The sender override removes the host device mounts and additional capabilities. It checks the compiled version, one running sender, full and ESP MQTT messages, settings preservation during an update, and rollback after a real one-shot process start failure. It records `run.log` and available MQTT and process logs in the output directory. The runner removes its named outer container and installer image and checks their absence; any nonzero exit is a failed acceptance, including a rollback failure.

The override excludes physical devices, so this test does not validate physical SMART readings. A pass does not establish PVE, systemd, ARM64, Windows, or release approval. ARM64 requires a separate actual ARM64 execution. PVE requires a separately provisioned disposable PVE guest and the official bootstrap; this directory does not yet automate that gate. The retained PVE recipe needs a verified cloud image, fresh guest credentials and virtual disks, signed PVE installation, DNS and TLS setup, lifecycle assertions, and shutdown and port cleanup checks.

`Dockerfile.installer-double` supplies the existing isolated fault-injection integration tests. Those tests substitute Docker commands and are not real Docker acceptance evidence. The loopback endpoint is test-only and serves an archive only after matching its digest and manifest identity.
