# Model library and downloads

[Documentation home](index.md) · [Shared assets](shared-assets.md) · [YAML sources](asset-sources.md)

The **Models** tab manages non-LoRA files in the external shared model library. It excludes the `loras/` subtree, which belongs to the dedicated **LoRAs** tab and `loras` CLI command. It does not copy models into every ComfyUI installation.

## Supported tasks

- browse installed model files and their relative paths;
- import a local file;
- download a catalog entry;
- view current and completed download tasks;
- delete a selected file from the shared library;
- add or remove catalog entries;
- add, enable, or edit download sources;
- open `model-sources.yaml` inside the TUI or in the system text editor;
- configure shared model and workflow paths.

The manager creates common directories such as `checkpoints`, `loras`, `vae`, `text_encoders`, `controlnet`, `diffusion_models`, `clip_vision`, and `upscale_models`.

## Model source YAML

The editable catalog is stored in `model-sources.yaml`.

```yaml
schema_version: 1
sources:
  - id: publisher
    name: Publisher downloads
    kind: direct-url
    base_url: https://example.org
    label: Official/Publisher
    enabled: true
entries:
  - id: example-checkpoint
    name: Example checkpoint
    source_id: publisher
    url: https://example.org/example.safetensors
    destination: checkpoints
    filename: example.safetensors
    required: false
    tags: [image, example]
```

A catalog entry is metadata, not the model itself. Models are downloaded only when the user asks or when a selected setup marks an entry as required.

## CLI examples

List installed files:

```bash
./comfyui-setup-manager models list
```

List sources and entries:

```bash
./comfyui-setup-manager --format yaml models sources
```

Import a model already on disk:

```bash
./comfyui-setup-manager models import ./my-model.safetensors \
  --destination checkpoints
```

Export a copy without removing the shared original:

```bash
./comfyui-setup-manager models export checkpoints/example.safetensors ./exports/
```

Download a known entry:

```bash
./comfyui-setup-manager models download example-checkpoint
```

Delete a model by its relative library path:

```bash
./comfyui-setup-manager models delete checkpoints/example.safetensors --yes
```

Open the YAML in the default editor:

```bash
./comfyui-setup-manager models edit-sources
```

## Safety

- File destinations are checked to prevent paths from escaping the shared library.
- Existing files are preserved unless overwrite is explicitly selected.
- Optional SHA-256 checksums are verified after download.
- Partial downloads use a temporary `.part` file and are removed after failure.
- Deleting a catalog entry does not delete an installed model file.
- Deleting an installed file requires a separate explicit action.

## Authentication

The built-in catalog contains public sources only. A service that requires an account or token should be disabled by default. Do not put secrets directly into a shareable profile or committed YAML file.


## LoRAs

LoRAs use `<shared-models>/loras`, `lora-sources.yaml`, and the parallel `loras` command group:

```bash
./comfyui-setup-manager loras list
./comfyui-setup-manager loras import ./style.safetensors
./comfyui-setup-manager loras sources
```

A Models command that targets `loras/` is rejected so the two workspaces cannot silently mix inventories or tasks.
