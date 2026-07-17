# Portable profile exports

ComfyUI Setup Manager writes `.comfyuisetup` exports here by default. This directory replaced the former project-level `setups/` name in v0.8.7; non-conflicting legacy files migrate automatically.

The export screen and CLI both allow another destination. Files exported here are also added to the manager profile library so they can be selected immediately.

New exports use a PEP 440 profile version and include their Python, accelerator-runtime, and PyTorch ABI tag in the recorded/displayed profile name. See [PEP 440 versions and ABI compatibility tags](../docs/abi-compatibility-tags.md).

Profiles can be edited from **Profile Library → Edit profile files** or with `profiles files/read-file/edit-file`. See [Manual profile and workflow authoring](../docs/manual-profile-workflow-authoring.md).
