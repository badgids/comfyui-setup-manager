# Portable setup profiles

[Documentation home](index.md) · [Profile format](../PROFILE_FORMAT.md) · [Manual authoring](manual-profile-workflow-authoring.md)

A `.comfyuisetup` file is a portable reconstruction manifest. It records the ComfyUI repository plus a minimal core overlay, custom-node identities and public sources, dependency manifests, and the verified Python package versions that produced a working installation.

Every new export also records a normalized PEP 440 version and a combined ABI tag. Exact profiles include the source Python version/ABI, CUDA/ROCm/CPU/MPS runtime, and full PyTorch build version; these values appear in the profile name, selector, Profile Library, archive metadata, and installation report. Read [PEP 440 versions and ABI compatibility tags](abi-compatibility-tags.md) before moving a compiled profile between machines.

## Included profiles

- **Vanilla ComfyUI** provides the general starting point and uses the manager prerequisites documented in [Prerequisites and platform preparation](prerequisites.md), plus any dependencies shown by its installation review.
- **Badgids Complete ComfyUI** is an exact NVIDIA/CUDA profile with additional video, audio, 3D, and native-build dependencies. Its requirements are intentionally isolated in [Badgids Complete profile](badgids-complete-profile.md) instead of being added to the manager's global prerequisite list.

Always review a profile's compatibility tag, system dependencies, selected nodes, and acceleration packages before installation. A profile requirement is not automatically a requirement for the manager or for another profile.

## What exact export records

The default export mode is exact. It records:

- the immutable ComfyUI Git commit;
- every installed Python distribution, including transitive packages such as `click`;
- the source Python major/minor, operating system, architecture, PyTorch accelerator/runtime family, and exact official PyTorch backend index;
- exact public-node Git commits that are known to be present in the source checkout's `origin/*` refs;
- compact Manager/Registry/Git descriptors for all resolvable public nodes;
- sanitized payloads only for plugins that remain unresolved after every configured and official lookup;
- original local-wheel provenance as metadata only; new exports resolve the package from configured/public indexes, Git sources, or build rules instead of archiving the wheel;
- model/workflow manifests and shared-library policy without machine-specific paths.

It does **not** infer the environment from node `requirements.txt` files. Those files are installation inputs, not a record of the final working environment, and they can omit manually installed or transitive dependencies.

## Duplicate custom-node roots

Two configured custom-node roots may contain folders with the same destination identity. A portable installation cannot place both at the same `custom_nodes/<identity>` destination, but the exporter no longer makes that conflict a dead end or silently chooses the first one.

In an interactive CLI export, every conflicting copy is displayed with its complete absolute path. Select the path number or numbers to omit, leaving exactly one copy. The TUI presents the same choice in a modal selector. Non-interactive automation can repeat:

```bash
--omit-node /complete/path/to/the/copy/to/omit
```

The selected node is exported normally. Source-tree Python distributions that belong to an explicitly omitted copy are excluded from the environment lock and listed under `explicitly_omitted_packages`, so the final package audit does not demand a package whose source tree was intentionally left out. The profile stores omission counts but does not embed machine-specific source paths.

## Why an exact export can stop

An exact profile is a reproducible installation plan, not a byte-for-byte backup. The exporter records the complete working package set, captures dependency manifests, resolves custom nodes to Registry/Manager/Git references, and stores only a compact Git-diff overlay for changes to the main ComfyUI source. It never copies `.venv`, site-packages, models, runtime data, or known public custom-node trees.

Export can still require a user decision when duplicate custom-node identities are present or when two installed distributions claim the same canonical package name. A genuinely unresolved local plugin may be embedded after every configured/public lookup fails; known nodes are never embedded merely because their local folder lacks `.git`.

## Public plugins and unresolved local payloads

The exporter first reads node `pyproject.toml`, package metadata, README links, local Git data, ComfyUI-Manager caches and snapshots, configured Manager channels and custom catalogs, the official Manager catalog, and the Comfy Registry. A public node is written as a small Manager/Registry id and/or validated GitHub repository descriptor. If a Manager snapshot or fetchable Git commit is available it is retained as an immutable ref. Missing `.git` data does not make a known public node eligible for embedding. Only a node that remains unidentified after all lookups receives a sanitized local payload.

Unresolved local payloads exclude `.git`, environments, caches, models, output data, secrets, and oversized runtime artifacts.

## Complete Python environment lock

`environment-lock.yaml` contains every installed distribution. The installer:

1. checks Python major/minor, OS, architecture, and PyTorch backend/runtime compatibility before changing the target;
2. installs the exact exported official PyTorch backend instead of choosing a different runtime from the receiving machine's toolkit;
3. installs exact index/Git packages and dedicated managed acceleration packages using the recorded source distribution family and version;
4. makes the entire lock the constraint set for ComfyUI, Manager, custom-node, and acceleration installs;
5. acquires custom nodes through their recorded Manager/Registry ids or validated GitHub repositories, honors a recoverable immutable ref when present, restores unresolved embedded plugins, then reinstalls editable or local Python distributions from safe reproduced source paths;
6. reapplies the lock after installers run;
7. audits dependency metadata and verifies every reproducible locked version;
8. runs ComfyUI's quick custom-node loader before reporting success.

Any exact ComfyUI or node ref actually recorded by the exporter remains mandatory at install time even if a general compatibility option requests unpinned refs. A public node without a recoverable immutable ref is installed through its recorded Manager/Registry id or validated GitHub repository and then checked by the locked environment and startup audit. Choosing an existing checkout is also exact when the profile records an exact core ref: its origin, commit, and clean state must match.

This prevents a node installer from upgrading NumPy, Transformers, or another shared dependency away from the version that worked in the source installation.

Repository-backed custom nodes are cloned directly. Their Manager/Registry IDs remain useful provenance, but the manager is not allowed to perform another global dependency solve. Manager-only entries use `--no-deps`. Once a complete exact environment lock has been installed, custom-node manifests are reconciled and preserved for audit but not installed again; this prevents node-by-node version churn. Dependency-only `install.py` scripts are skipped, while required bootstrap scripts such as `comfy-env` remain enabled.

Captured requirement files are not blindly combined with the exact lock. Before each core, Manager, or custom-node requirements installation, the manager compares every ordinary package specifier with the version verified in the source environment. If a repository manifest now requests a different exact version or excludes the verified version, the generated requirements copy under `.comfy-setup/` is reconciled to the verified version and the override is printed in the installation console/report. This prevents uv from receiving an impossible manifest-plus-constraint pair while retaining a zero-difference final package audit.

## Export a working setup

```bash
comfyui-setup-manager profiles from-installation /path/to/ComfyUI \
  --name "Studio Setup" \
  --publisher "Your Name"
```

To resolve a known duplicate non-interactively:

```bash
comfyui-setup-manager profiles from-installation /path/to/ComfyUI \
  --name "Studio Setup" \
  --omit-node /path/to/shared/custom_nodes/duplicate-node
```

Without `--output`, the file is written to `<project-root>/profiles/studio-setup.comfyuisetup`. Pass `--output PATH` to choose another file or directory. Successful exports are imported into the manager profile library immediately. The former project `setups/` directory is migrated without overwriting existing profile files.

Use `--skip-local-plugins` only for a deliberately non-exact/partial export. The duplicate-node resolver is different: it permits an explicit omission only when multiple source trees compete for the same portable destination identity, and it records that decision instead of silently dropping a copy.

## Re-export older profiles

Profiles generated by early 0.8.4 exporters may contain only a short compatibility-pin list and top-level package guesses. Those files remain supported, but the package versions, source-tree install metadata, exact PyTorch backend source, ABI identity, and local artifacts that were never stored cannot be reconstructed from the old archive. Create a new profile from the original working installation with 0.8.7 to capture the corrected full lock and compatibility tag.

## Import and remove

```bash
comfyui-setup-manager profiles import ./studio.comfyuisetup
comfyui-setup-manager profiles remove studio-setup --yes
```

The TUI refreshes the Setup & Install selector immediately after import or export. The Profile Library can remove imported profiles after confirmation; built-in profiles are protected. **Edit profile files** opens a syntax-aware editor for UTF-8 archive members. Saving is transactional: the candidate is rebuilt, all integrity values are regenerated, and the original is replaced only after validation succeeds. CLI equivalents are `profiles files`, `profiles read-file`, and `profiles edit-file`.

## Install

```bash
comfyui-setup-manager install run \
  --profile ./studio.comfyuisetup \
  --target /path/to/new/ComfyUI \
  --repository-mode profile
```

An exact profile is intentionally tied to a compatible runtime family. Use a newly exported profile for a different Python minor, operating system, or architecture rather than allowing silent dependency substitution.

## Shared libraries and asset manifests

New portable setup files may contain:

- `environment-lock.yaml` for the complete Python distribution lock;
- `custom_nodes.yml` for Registry/Manager/Git node references;
- `dependency-manifests.yml` plus copied requirements/pyproject/uv lock files;
- `comfyui_overlay/` for changed core files only;
- `models.yaml` for required or suggested model entries;
- `workflows.yaml` for required or suggested workflows;
- `asset-sources.yaml` for public download source metadata;
- `libraries.yaml` for shared-library policy;
- `profile.yaml` for ComfyUI, Python/PyTorch, and installation policy;
- `custom_nodes.yml` as the sole custom-node inventory;
- `dependency-manifests.yml` plus copied `requirements*.txt`, `pyproject.toml`, `python.toml`, and `uv.lock` files for dependency audit and reconstruction;
- `embedded_plugins/` for sanitized source belonging only to unresolved local plugins;
- `embedded_wheels/` only when reading an older compatible profile that already contains a last-resort wheel payload; new reference-first exports do not use it as a normal package-distribution mechanism.

The archive does not store the user's external model/workflow library path. The receiving computer chooses its own paths during review.
