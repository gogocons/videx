# Videx Development Guidelines

## Project Principles

- Videx is an offline-first YouTube bookmark manager that generates standalone HTML bookmarks and a searchable local index.
- Preserve offline functionality and portability of generated bookmarks.
- Maintain compatibility with macOS and Linux.
- Avoid unnecessary dependencies, external services, and architectural complexity.

## Compatibility and Data Safety

- Preserve existing command-line interfaces and bookmark formats unless changes are explicitly approved.
- Keep the application source separate from user bookmark libraries.
- Preserve backup behavior and preview/dry-run defaults for bulk operations.
- Require explicit `--apply` for supported operations that modify existing library data.
- Treat bookmark metadata, tags, collections, and catalog entries as persistent user data.

## Development Standards

- Prefer focused changes over broad refactoring.
- Add regression tests when correcting defects.
- Use synthetic fixtures for testing; never assume access to a user's real bookmark library.
- Update documentation when installation, configuration, or user-facing behavior changes.
