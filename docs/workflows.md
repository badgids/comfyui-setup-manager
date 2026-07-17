# Workflow library, packs, and downloads

[Documentation home](index.md) · [Shared assets](shared-assets.md) · [YAML sources](asset-sources.md) · [Workflow format](../WORKFLOW_FORMAT.md) · [Manual authoring](manual-profile-workflow-authoring.md)

ComfyUI workflows remain native JSON files. The manager does not replace ComfyUI's workflow format.

The pack extension is `.comfyworkflows`; `.workflows` refers only to a directory name, not another exchange format. The [manual authoring guide](manual-profile-workflow-authoring.md) shows the archive manifest and checksum rules.

## Workflows tab

The dedicated **Workflows** tab can:

- browse the external workflow library;
- browse nested directories;
- import native `.json` workflows;
- import `.comfyworkflows` packs;
- export one workflow as normal JSON;
- create directory-preserving workflow packs;
- download catalog entries;
- show download tasks;
- delete selected workflow files;
- edit `workflow-sources.yaml` in the TUI;
- open the YAML in the system editor;
- open the shared-path setup screen.

## Shared workflow directory

The manager recommends one external workflow library for all managed ComfyUI installations. Each installation's native `user/default/workflows` path points to that external library.

This means a workflow imported once appears in every connected installation. See [Shared models and workflows](shared-assets.md).

## Workflow packs

A `.comfyworkflows` file is a ZIP-compatible archive that preserves the source directory tree.

Example:

```text
bundle.yaml
README.md
workflows/
  image/portraits/base.json
  video/wan/example.json
models.yaml
nodes.yaml
asset-sources.yaml
setup.comfyuisetup
```

The archive may include README files, previews, metadata, model requirements, node requirements, source catalogs, and a setup profile or setup URL.

It does not silently install dependencies. The import review shows required and suggested items before the user chooses what to apply.

## CLI examples

List workflows:

```bash
./comfyui-setup-manager workflows list
```

Inspect a JSON workflow:

```bash
./comfyui-setup-manager --format yaml workflows inspect ./workflow.json
```

Import a workflow or pack:

```bash
./comfyui-setup-manager workflows import ./my-pack.comfyworkflows
```

Create a pack while preserving subdirectories:

```bash
./comfyui-setup-manager workflows pack ./workflow-library \
  --name "My Workflow Pack" \
  --output ./my-workflows.comfyworkflows
```

Include companion YAML files:

```bash
./comfyui-setup-manager workflows pack ./workflow-library \
  --name "Complete Pack" \
  --requirements-yaml ./models.yaml \
  --requirements-yaml ./nodes.yaml \
  --requirements-yaml ./asset-sources.yaml \
  --output ./complete.comfyworkflows
```

Download a catalog workflow:

```bash
./comfyui-setup-manager workflows download example-workflow
```

Delete one shared workflow:

```bash
./comfyui-setup-manager workflows delete image/examples/example.json --yes
```
