# Security policy

[Back to README](README.md) · [Documentation](docs/index.md)

## Supported versions

Security fixes are applied to the newest release line. Older development archives should be replaced rather than patched in place.

## Reporting a problem

Report security issues privately to the project creator through the contact options on [Alan Guice's GitHub profile](https://github.com/badgids). Do not publish credentials, private paths, or a working exploit in a public issue.

## Trust boundaries

The manager can execute package managers, Git, Python installers, compilers, and plugin installers. Review setup profiles and wheel sources before applying an unfamiliar setup.

## Source policy

Normal index packages use approved public Python package infrastructure. Accelerated PyTorch uses official PyTorch distribution indexes. Profiles retain immutable Git package commits. New exports do not archive local wheels; older compatible profiles with embedded wheel payloads remain checksum-verified. Custom nodes are resolved against trusted local Manager metadata, configured catalogs, the official Manager catalog, the Comfy Registry, and validated GitHub repositories; sanitized custom-node source is embedded only when no trusted public or configured identity can be established. Editable wheel sources carry explicit trust labels.

Requirements files are rejected when they attempt to override package indexes or inject unsupported download locations.

## Archive safety

Setup and workflow archives reject absolute paths, traversal paths, unsafe links, oversized payloads, invalid checksums, and unexpected embedded source. Exact profiles reject incomplete package locks, ambiguous canonical distributions, unresolved duplicate-node choices, and unsafe source-tree install paths. Legacy embedded-wheel payloads are accepted only after checksum and archive-path validation.

## Destructive operations

Uninstall and snapshot deletion require explicit confirmation. Uninstall rejects filesystem roots, home directories, shallow unsafe paths, and non-ComfyUI targets.

## Secrets

Passwords must not be placed in command arguments. The TUI uses masked input. The CLI supports reading a privilege password from a named environment variable when necessary. Logs and exports should be reviewed before public sharing.

## Third-party code

Known public custom nodes are installed through their recorded Registry/Manager id or validated GitHub repository, using a Manager snapshot/Git commit when one can be recovered. Missing local `.git` metadata alone never causes a public node to be bundled. Only unresolved local node trees are carried as sanitized payloads. All third-party plugins run with the permissions of the user; review unfamiliar profiles before installation.
