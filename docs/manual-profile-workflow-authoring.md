# Manual profile and workflow authoring

[Documentation home](index.md) · [Profile format](../PROFILE_FORMAT.md) · [Workflow format](../WORKFLOW_FORMAT.md)

This guide is for users who want to create or edit the exchange files by hand. The manager remains the safest way to generate them because it writes checksums and validates the final archive, but neither format is opaque.

## File names and terminology

- `.comfyuisetup` is the ZIP-compatible portable setup-profile format.
- `.json` is the native format for one ComfyUI workflow.
- `.comfyworkflows` is the ZIP-compatible format for a pack of workflows and support files.
- A directory named `workflows` is a normal ComfyUI workflow directory. `.workflows` is not a separate file format; use `.json` for one workflow or `.comfyworkflows` for a pack.
- New profile exports default to `<project-root>/profiles/`. On first use, the manager moves non-conflicting files from the old `<project-root>/setups/` directory into `profiles/`.

Archive paths always use forward slashes. Do not include absolute paths, `..`, environments, credentials, models, output files, or caches.

## Edit a `.comfyuisetup` safely

In the TUI, open **Setup & Install → Profiles**, choose an imported `.comfyuisetup`, and select **Edit profile files**. Choose a UTF-8 text member, edit it with syntax coloring, then select **Save, rebuild & validate**. The manager writes a separate candidate, regenerates checksums, validates it, and replaces the original only after every check passes. `metadata.yaml`, binary files, and text files over 2 MiB are protected. Changing the profile `id` in place is blocked because the imported-library filename and removal key use that ID.

The same service is available without the TUI:

```bash
comfyui-setup-manager profiles files ./profiles/studio.comfyuisetup
comfyui-setup-manager profiles read-file ./profiles/studio.comfyuisetup profile.yaml
comfyui-setup-manager profiles edit-file ./profiles/studio.comfyuisetup profile.yaml --from-file ./edited-profile.yaml
```

Use `--from-file -` to read replacement text from standard input. `--allow-id-change` is intended only for an external copy that will be imported as a new profile.

The canonical writer treats these companion files as authoritative for their named sections:

| Archive member | Authoritative profile section |
|---|---|
| `environment-lock.yaml` | `environment_lock` |
| `custom_nodes.yml` | `nodes` |
| `dependency-manifests.yml` | `dependency_manifests` |
| `models.yaml` | `models` |
| `workflows.yaml` | `workflows` |
| `asset-sources.yaml` | `asset_sources` |
| `libraries.yaml` | `libraries` |

If the same section also appears in `profile.yaml`, the companion file wins while loading. Avoid duplicate declarations.

## Create a minimal `.comfyuisetup` by hand

Create a new directory and add `profile.yaml`:

```yaml
schema_version: 4
kind: comfyui-setup-profile
id: my-portable-profile
name: My Portable Profile [cp312-accelany-torch2_8_0]
display_name: My Portable Profile [cp312-accelany-torch2_8_0]
version: 1.0.0
compatibility:
  profile_version: 1.0.0
  pep440: true
  exact: false
  python_version: '3.12'
  python_abi: cp312
  accelerator: any
  backend_version: ''
  accelerator_abi: accelany
  pytorch_version: 2.8.0
  pytorch_abi: torch2_8_0
  abi_tag: cp312-accelany-torch2_8_0
publisher: Your Name
description: A manually authored flexible profile.
comfyui:
  repository: https://github.com/Comfy-Org/ComfyUI.git
  branch: master
python:
  preferred: '3.12'
  fallbacks: ['3.11', '3.13']
torch:
  version: 2.8.0
  fallback_unpinned: true
constraints: {}
extra_python_packages: []
nodes: []
accelerated_packages: []
post_install:
  generate_launchers: true
  validate_imports: [torch]
models: []
workflows: []
asset_sources: {}
libraries: {}
```

`version` and `compatibility.profile_version` must be PEP 440 compatible and normalize to the same value. The ABI tag uses tag-safe letters, numbers, dots, underscores, and hyphens. See [PEP 440 versions and ABI compatibility tags](abi-compatibility-tags.md).

Package the directory without adding an extra parent folder:

```python
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

source = Path("my-profile")
output = Path("profiles/my-portable-profile.comfyuisetup")
output.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(source).as_posix())
```

`profile.yaml` is the only required archive member for a simple flexible profile. `metadata.yaml` is generated metadata, so omit it rather than inventing a checksum. Use the manager's editor or exporter when adding checksummed dependency manifests, embedded unresolved plugins, core overlays, or legacy wheels.

Validate before sharing:

```bash
comfyui-setup-manager profiles show ./profiles/my-portable-profile.comfyuisetup
comfyui-setup-manager profiles import ./profiles/my-portable-profile.comfyuisetup
```

## Profile checksums for advanced manual edits

- `metadata.yaml.sha256` is SHA-256 of the exact stored `profile.yaml` bytes.
- Each dependency-manifest entry hashes the exact referenced file bytes.
- Each embedded wheel hashes its exact wheel bytes.
- An embedded directory or core overlay hashes entries sorted by relative path. For every entry, hash the UTF-8 relative path, one NUL byte, the file bytes, and another NUL byte.

The built-in editor recalculates all of these. A raw ZIP editor does not. Never leave an old checksum after changing its payload.

## Create one native workflow by hand

A native workflow is ordinary JSON. A saved UI workflow normally has a `nodes` array; an API-format workflow normally maps node IDs to objects containing `class_type`. Keep the `.json` extension and validate it:

```bash
comfyui-setup-manager workflows inspect ./portrait.json
```

Absolute model or filesystem paths are reported for review because another machine may not have them.

## Create a `.comfyworkflows` pack by hand

A pack stores `bundle.yaml`, an optional `README.md`, and native JSON files below `workflows/`. This script creates a valid one-workflow pack and calculates both checksums:

```python
from hashlib import sha256
from json import loads
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import yaml

workflow_path = Path("portrait.json")
output = Path("portrait-pack.comfyworkflows")
payload = workflow_path.read_bytes()
workflow = loads(payload.decode("utf-8"))
is_saved = isinstance(workflow.get("nodes"), list)
node_count = len(workflow.get("nodes", [])) if is_saved else sum(
    isinstance(value, dict) and "class_type" in value for value in workflow.values()
)
readme = "# Portrait pack\n\nA manually authored ComfyUI workflow pack.\n"
manifest = {
    "schema_version": 3,
    "kind": "comfyui-workflow-bundle",
    "id": "portrait-pack",
    "name": "Portrait pack",
    "description": "A manually authored workflow pack.",
    "publisher": "Your Name",
    "tags": ["portrait"],
    "payload_root": "workflows",
    "default_install_subdirectory": "portraits",
    "workflow_count": 1,
    "support_file_count": 0,
    "workflows": [{
        "path": workflow_path.name,
        "sha256": sha256(payload).hexdigest(),
        "format": "save" if is_saved else "api",
        "node_count": node_count,
        "required_node_types": [],
        "model_references": [],
        "warnings": [],
    }],
    "support_files": [],
    "readme_path": "README.md",
    "readme_sha256": sha256(readme.encode("utf-8")).hexdigest(),
    "setup": {"embedded_path": None, "embedded_sha256": None, "reference": None},
    "models_included": False,
    "public_plugins_included": False,
    "directory_structure_preserved": True,
    "companion_yaml": [],
    "companion_files": [],
}
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    archive.writestr("bundle.yaml", yaml.safe_dump(manifest, sort_keys=False))
    archive.writestr("README.md", readme)
    archive.writestr(f"workflows/{workflow_path.name}", payload)
```

Validate the result:

```bash
comfyui-setup-manager workflows inspect ./portrait-pack.comfyworkflows
```

For several workflows, add one manifest entry and one `workflows/<relative-path>.json` member per file. `path` is relative to the `workflows/` payload root. Preserve nested paths in both places.

## Edit a workflow pack manually

After changing a workflow, recompute its exact byte checksum and update the matching `workflows[].sha256` value in `bundle.yaml`. Also update:

- `workflow_count` when workflows are added or removed;
- `support_files`, `support_file_count`, and each support checksum/size;
- `readme_sha256` after changing the README;
- `companion_files` after changing companion YAML;
- `setup.embedded_sha256` after changing an embedded `.comfyuisetup`.

Re-ZIP the contents with `bundle.yaml` at the archive root, then run `workflows inspect`. The importer rejects path traversal, invalid JSON/YAML, missing members, and checksum mismatches.
