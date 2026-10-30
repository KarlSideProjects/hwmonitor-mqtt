# Sender candidate inspection

No Sender package exists in this repository. The public entrance consists only of `release-public/README.md` and `release-public/LICENSE-NOTICE.md`; it ships no runtime or third-party components. Windows x64, Linux x64 and Linux ARM64 remain unavailable until their real artifacts and installation paths pass the release gates.

`python3 scripts/inspect_sender_candidate.py <platform> <unpacked-directory>` checks a deliberately narrow proposed layout. Platform is `linux-x64`, `linux-arm64` or `windows-x64`. The directory must contain exactly `sender` (ELF prefix) or `sender.exe` (PE prefix) and these nonempty notice files: `licenses/python.txt`, `licenses/paho-mqtt.txt`, `licenses/psutil.txt`, and `licenses/python-dotenv.txt`. Windows also requires `licenses/pythonnet.txt` and `licenses/wmi.txt`. The checker rejects other paths, links, common archive signatures, selected plaintext credential patterns, empty files and notices whose bytes do not match its embedded trusted hashes. The allowlist is owned by the checker, not by the candidate. It does not accept archives as input.

These filenames are a preliminary contract for test samples, not an inventory of a built package. A successful check is not release approval. Executable prefixes do not prove architecture, compilation, authorship, or absence of embedded Python or secrets. Matching these pinned notice bytes does not establish the completeness or applicability of notices for a future package. The checker cannot establish license compliance, archive safety after independent packaging, or detect every secret. Release maintainers must inspect real unpacked artifacts and published archives, establish provenance and all actual files and dependencies, review notices and corresponding-source duties, and revise this contract and its tests as necessary. Never publish solely because this command succeeds.

## Dependency inputs to investigate

`uv.lock` resolves paho-mqtt 2.1.0, psutil 7.1.1, python-dotenv 1.1.1, pygame 2.6.1, pythonnet 3.0.5, clr-loader 0.2.10, WMI 1.5.1, and pywin32 311. These are project lock entries, not proof that any item enters a Sender package. The selected Python runtime and Nuitka version are not fixed by a Sender build specification. Additional transitive or native components may be included by a compiler or runtime.

The current Dockerfile installs psutil, paho-mqtt and python-dotenv without pinned versions. The existing Windows installer fetches LibreHardwareMonitor 0.9.6 and PawnIO setup 2.2.0 from their upstream release URLs with fixed SHA-256 values. Those are existing installation inputs, not evidence that either will be bundled in a future release.

Before publishing each actual platform package, inventory every component and version from the final artifact, including Python, native libraries, paho, pygame if present, pythonnet and its dependencies if present, LibreHardwareMonitor, PawnIO, and build tool runtime inclusions. Obtain authoritative license texts and notices for those exact versions, determine any source and offer obligations and compatibility, and retain evidence for the actual distribution or external download method. The [Paho license notice](https://github.com/eclipse-paho/paho.mqtt.python/blob/v2.1.0/LICENSE.txt), [LibreHardwareMonitor license](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.6/LICENSE), and [PawnIO source](https://github.com/namazso/PawnIO) are starting points, not proof of future package contents or compliance. The PawnIO setup archive and its exact corresponding driver sources require separate verification.

## Trusted sample notice inputs

The checker accepts exact SHA-256 values embedded in its source. The positive test notices are in `tests/fixtures/release-notices/`. They are inputs for the sample layout, not a complete third-party inventory. The Python text comes from [CPython v3.11.15](https://github.com/python/cpython/blob/v3.11.15/LICENSE). The other texts come from the named PyPI source distributions, each checked against the SHA-256 published in its PyPI release metadata:

| File | Source distribution | Source archive SHA-256 | Selected content |
| --- | --- | --- | --- |
| `paho-mqtt.txt` | [paho-mqtt 2.1.0](https://pypi.org/project/paho-mqtt/2.1.0/#files) | `12d6e7511d4137555a3f6ea167ae846af2c7357b10bc6fa4f7c3968fc1723834` | `LICENSE.txt`, then `edl-v10`, joined with a newline |
| `psutil.txt` | [psutil 7.1.1](https://pypi.org/project/psutil/7.1.1/#files) | `092b6350145007389c1cfe5716050f02030a05219d90057ea867d18fe8d372fc` | `LICENSE` |
| `python-dotenv.txt` | [python-dotenv 1.1.1](https://pypi.org/project/python-dotenv/1.1.1/#files) | `a8a6399716257f45be6a007360200409fce5cda2661e3dec71d23dc15f6189ab` | `LICENSE` |
| `pythonnet.txt` | [pythonnet 3.0.5](https://pypi.org/project/pythonnet/3.0.5/#files) | `48e43ca463941b3608b32b4e236db92d8d40db4c58a75ace902985f76dac21cf` | `LICENSE` |
| `wmi.txt` | [WMI 1.5.1](https://pypi.org/project/WMI/1.5.1/#files) | `b6a6be5711b1b6c8d55bda7a8befd75c48c12b770b9d227d31c1737dbf0d40a6` | `readme.rst`, which states copyright and license; it is not a complete license text |

The WMI source distribution identifies MIT licensing but does not include a standalone license text. This sample does not establish what text or other materials an actual Windows distribution must provide. Every real package remains blocked on the complete version-specific obligation review described above, including WMI, pywin32, clr-loader, LibreHardwareMonitor, PawnIO and the actual Python runtime.
