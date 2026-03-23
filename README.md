# KiCad Extract Project Local Library Plugin

## Overview
This KiCad Action Plugin extracts all **used components** from the currently opened project and converts them into a **fully self-contained local library structure**.

The plugin collects:
- Symbols
- Footprints
- 3D STEP models

It then reorganizes them into:
- Per-component folders (for traceability and version control)
- A combined project-local symbol library
- A combined project-local footprint library

This enables:
- Fully portable KiCad projects
- Reproducible builds
- Clean Git repositories without external dependencies

---

## Features

### ✔ Extract Used Components
- Parses schematic (`.kicad_sch`)
- Detects all used symbols
- Resolves original libraries

### ✔ Per-Component Structure
Each component is stored in its own folder:

```
components/<component_name>/
  <component_name>.kicad_sym
  <component_name>.kicad_mod
  <component_name>.step
```

### ✔ Local Libraries
Creates project-local libraries:

```
localLibs/localLib.kicad_sym
localLibs/localLib.pretty/
localLibs/3d/
```

### ✔ 3D Model Handling
- Copies STEP files
- Rewrites paths to:

```
${KIPRJMOD}/localLibs/3d/<component>.step
```

### ✔ Deterministic Output
- Stable file naming
- Repeatable results (idempotent)

### ✔ Safe Operation
- Never modifies original libraries
- Only reads and copies data

---

## Installation

1. Locate your KiCad plugin directory:

- Linux:
  `~/.local/share/kicad/10.0/scripting/plugins`
- Windows:
  `%APPDATA%\kicad\10.0\scripting\plugins`
- macOS:
  `~/Library/Application Support/kicad/10.0/scripting/plugins`

2. Copy the plugin folder:

```
ExtractProjectLocalLibrary/
  __init__.py
  plugin.py
  dialog.py
  ...
  icon.png
  README.md
```

3. Restart KiCad

---

## Usage

1. Open your KiCad project
2. Open PCB Editor (recommended)
3. Go to:

```
Tools → External Plugins → Extract Project Local Library
```

4. (Optional) Configure options in dialog
5. Click **Run**

---

## Output Structure

```
project_root/
  components/
  localLibs/localLib.kicad_sym
  localLibs/localLib.pretty/
  localLibs/3d/
```

---

## Configuration Options (if enabled)

- Copy footprints
- Copy STEP files
- Rebuild symbol library
- Rebuild footprint library
- Rewrite 3D paths
- Overwrite existing files
- Dry-run mode

---

## Limitations

- Complex symbol inheritance may be flattened
- Missing footprints or models are skipped with warnings
- Requires valid KiCad project files

---

## Logging

The plugin provides clear logs:

```
Info: Processing symbol AO4301
Warning: Missing footprint XYZ
Error: Symbol not found
```

---

## Advanced Use

### CI/CD
This plugin can be used in automated workflows to:
- Freeze dependencies
- Ensure reproducible builds

### Git Integration
- Commit `components/`
- Commit `localLibs/`
- Ignore global libraries

---

## License
MIT (or specify your preferred license)

---

## Author
Generated with AI assistance

---

## Future Improvements
- Multi-library merging improvements
- Better alias handling
- GUI progress bar
- SVG icon support
