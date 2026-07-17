# Model, LoRA, and workflow source YAML

[Documentation home](index.md) · [Models and LoRAs](models.md) · [Workflows](workflows.md) · [Configuration](configuration.md)

Download sources and catalog entries are ordinary YAML so they can be reviewed, shared, versioned, and edited without changing Python code.

## Files

| File | Purpose |
|---|---|
| `model-sources.yaml` | Non-LoRA model, VAE, encoder, and related sources and entries |
| `lora-sources.yaml` | LoRA sources and entries |
| `workflow-sources.yaml` | Workflow JSON and workflow-pack sources and entries |
| `download-tasks.yaml` | Persisted download history and task state |
| `asset-paths.yaml` | External models and workflows library locations |

The program creates user-editable copies in its configuration directory. Built-in copies provide safe defaults.

## Source fields

```yaml
- id: github
  name: GitHub releases
  kind: direct-url
  base_url: https://github.com
  label: Official/Publisher
  enabled: true
  notes: Use direct public release or raw-file links.
```

Common `kind` values:

- `direct-url`: a complete HTTP or HTTPS URL;
- `huggingface`: a path resolved relative to a Hugging Face base URL;
- `local`: a file already present on the computer.

## Entry fields

```yaml
- id: my-workflow
  name: My workflow
  source_id: github
  url: https://raw.githubusercontent.com/example/repo/main/workflow.json
  destination: image/examples
  filename: workflow.json
  sha256: optional-lowercase-checksum
  description: A short explanation.
  required: false
  tags: [image, example]
```

`destination` is always relative to the selected shared library. Absolute destinations and parent-directory escapes are rejected.

## Editing inside the TUI

The Models, LoRAs, and Workflows tabs each include their own **Edit sources** action. The YAML editor:

- shows the complete file;
- validates YAML before saving;
- reports line and parsing errors;
- preserves the file if validation fails.

## Editing in the system editor

Use the tab action **Open in editor**, or run:

```bash
./comfyui-setup-manager config edit model-sources.yaml
./comfyui-setup-manager config edit lora-sources.yaml
./comfyui-setup-manager config edit workflow-sources.yaml
```

## Portable setup and workflow packs

A `.comfyuisetup` profile can contain:

- `models.yaml`;
- `workflows.yaml`;
- `asset-sources.yaml`;
- `libraries.yaml`;
- node and Python dependency information.

A `.comfyworkflows` archive can include companion YAML such as `models.yaml`, `nodes.yaml`, `asset-sources.yaml`, and setup references.

Public model, LoRA, and workflow files are not embedded automatically. The manifests explain where to obtain them. This keeps archives small and portable.
