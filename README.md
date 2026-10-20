# q2q source XNU build harness

This is a source-only compilation harness for the experimental Samsung Galaxy Z Fold3 port. It fetches pinned public sources and builds ARM64 XNU10063.141.1, DEVELOPMENT/BCM2837, with the UniXNU4KiB patches, ArmDTPlatform, ArmCpu and the source crypto provider.

It does not contain a usable OS, Apple firmware, an IPSW, device dumps, serial numbers, keys, recovery images or partition backups. It never accesses a connected phone. No UFS, RAM-disk storage, GPU or sheng power-management driver is enabled. The first objective is a source kernel for early CPU/console bring-up; it cannot boot iPadOS userspace by itself.

The GitHub Actions workflow is manually triggered only, uses macos-15 and Xcode16.4, two build jobs, a90minute timeout and3day artifact retention. Action revisions and source revisions are recorded. It downloads the sources directly on the runner. Artifacts contain only the resulting source kernel, reports and logs; SDK files and proprietary Apple kernelcache are excluded.

Local preparation without macOS:

```sh
python3.12 scripts/ci-source-kernel.py --work /path/without/spaces/q2q-source --prepare-only
```

Compilation is not yet validated on a macOS runner. A prepared tree or a green syntax check is not a successful kernel build. This harness uses the public third-party corecrypto-2023 source snapshot pinned by UniXNU; its provenance is recorded by the upstream builder and is not represented as Apple-authenticated.
