# Workflow and workflow-pack format

[Main README](README.md) · [Workflow guide](docs/workflows.md) · [Manual authoring](docs/manual-profile-workflow-authoring.md) · [Shared assets](docs/shared-assets.md)

## One workflow

One workflow remains a normal ComfyUI `.json` file. The manager validates it but does not wrap it in a proprietary format.

The canonical multi-workflow extension is `.comfyworkflows`. A folder named `workflows` is normal, but `.workflows` is not a separate file format. See [Manual profile and workflow authoring](docs/manual-profile-workflow-authoring.md) for by-hand examples and checksum rules.

## Workflow packs

A `.comfyworkflows` file is a ZIP-compatible archive for one or more workflows. It preserves nested directory paths.

```text
bundle.yaml
README.md
workflows/
  image/portraits/basic.json
  video/wan/example.json
previews/
models.yaml
nodes.yaml
asset-sources.yaml
libraries.yaml
setup.comfyuisetup
```

## `bundle.yaml`

The manifest records:

- archive schema and name;
- publisher, description, and tags;
- default relative destination;
- each workflow's archive path, checksum, format, node types, and model references;
- support files;
- included companion YAML;
- an embedded or external setup reference.

## Directory preservation

A workflow stored at:

```text
workflows/vehicles/hot-rods/build.json
```

installs as:

```text
<chosen workflow root>/vehicles/hot-rods/build.json
```

The importer does not flatten paths.

## Companion YAML

A pack may include:

- `models.yaml` — required or suggested models and LoRAs;
- `nodes.yaml` — required or suggested custom-node sources;
- `asset-sources.yaml` — public model/workflow sources;
- `libraries.yaml` — shared-library recommendations;
- other approved metadata YAML.

These documents describe dependencies. Large models and public plugin repositories are not embedded in the workflow pack.

## Setup references

A pack may:

- include one `.comfyuisetup` file;
- include a direct public download URL for one;
- reference a GitHub repository or release page;
- contain both an embedded setup and external documentation.

The manager asks before importing or applying a setup. It never silently modifies a ComfyUI installation while the user is only browsing a workflow pack.

## README and metadata

README, Markdown, JSON, YAML, TOML, CSV, text, and preview images may be included within configured size limits. They preserve their relative paths.

## Safety

- path traversal and absolute archive paths are rejected;
- checksums are validated;
- existing destination files are preserved unless overwrite is selected;
- models, virtual environments, outputs, secrets, and executable plugin repositories are excluded;
- absolute machine paths inside workflow JSON are reported for review rather than silently changed.
