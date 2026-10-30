# Shared distribution contracts

The committed source bootstraps obtain project source, not a compiled release. Linux copies source while preserving selected local settings; Windows retains an existing installation rather than updating its code. The Dockerfile copies the Python package into its image. See `bootstrap.sh`, `bootstrap.ps1`, and `Dockerfile` for the authoritative behavior.

The root `LICENSE` contains GPL version 3 and `README.md` identifies it as the project license. Project dependency declarations do not establish the licenses or exact contents of a future compiled distribution. No compiled release artifact is established by this baseline.
