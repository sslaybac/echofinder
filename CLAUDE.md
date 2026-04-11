# Echofinder

## Package Management
Use `uv` exclusively for all Python package management. Never use pip directly.

- Add dependencies: `uv add <package>`
- Remove dependencies: `uv remove <package>`
- Run scripts: `uv run <script>`
- Run the app: `uv run python -m echofinder`

## Project Structure
Follow the Model-View separation defined in Implementation Details v3:
- `echofinder/` — main package
  - `models/` — data layer (file metadata, hash cache, duplicate groups)
  - `services/` — business logic (hashing, file type resolution, permission evaluation)
  - `ui/` — all PyQt6 widgets and UI components

## Stage 1 Scope
Building the application skeleton and file tree only. Do not implement hashing,
duplicate detection, or any preview widget beyond the empty state widget.
