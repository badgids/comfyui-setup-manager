Perform the release safety audit:

1. Search source, docs, wheel, ZIP, and TAR.GZ for personal paths, credentials, private hosts, and unrelated service names.
2. Confirm no `.venv`, models, workflows, outputs, or cache directories are packaged accidentally.
3. Confirm built-in YAML profiles and configuration are in the wheel exactly once.
4. Confirm clean public plugins use immutable refs and any snapshot payload is justified by dirty/unpinned source and passes sanitization.
5. Confirm destructive CLI commands require explicit confirmation.
6. Confirm ZIP and TAR.GZ extract safely and checksums match.

7. Confirm no models, LoRAs, workflows, or external asset directories are packaged.
8. Confirm `asset-paths.yaml`, source catalogs, and task YAML are included exactly once as package data.
9. Confirm portable archives do not include machine-specific shared-library paths.
10. Confirm updates skip uv for current/file-only candidates, never invoke upstream requirement files independently, normalize duplicate installed metadata, keep pending-update patch/force actions accessible, and automatically restore failed mutated candidates.
11. Confirm exact profiles carry matching PEP 440 profile versions and Python/accelerator/PyTorch ABI tags in profile, archive metadata, and installation reports.
