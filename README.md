# KiCad Localizer Plugin

## Overview
A KiCad Action Plugin that turns a project into a self-contained, portable set of local libraries.

The plugin:
- Exports used components into per-component folders
- Builds local project libraries in `localLibs/`
- Writes `sym-lib-table` and `fp-lib-table`
- Rewrites schematic and PCB references to point to `localLib`
- Validates 3D model paths and logs errors when paths do not point to `localLibs/3d/`

## Output structure

```text
project_root/
  components/
    <component>/
      <component>.kicad_sym
      <component>.kicad_mod
      <component>.step

  localLibs/
    localLib.kicad_sym
    localLib.pretty/
      <component>.kicad_mod
    3d/
      <component>.step

  sym-lib-table
  fp-lib-table
```

## What gets rewritten

- **Schematic**
  - `lib_id` for exported components → `localLib:<component>`
  - Footprint property for exported components → `localLib:<component>`

- **PCB**
  - Footprint library link → `localLib:<component>`
  - 3D model paths → `${KIPRJMOD}/localLibs/3d/<component>.step`

## Logging and validation

During a run the plugin produces:
- Export totals (symbols, footprints, steps)
- 3D rewrite totals for `localLib.pretty` and PCB
- Per-component transition lines: `Component: OldLib -> NewLib`
- Before/after summary of model references
- `ERROR` if any model path does not start with `${KIPRJMOD}/localLibs/3d/`

## Installation

Copy the plugin folder into the KiCad scripting plugins directory and restart KiCad.

Common paths:
- macOS: `~/Documents/KiCad/9.0/scripting/plugins`
- Linux: `~/.local/share/kicad/9.0/scripting/plugins`
- Windows: `%APPDATA%\kicad\9.0\scripting\plugins`

## Usage

1. Open the project in KiCad
2. Open the PCB Editor
3. Run the **KiCad Localizer** Action Plugin
4. Review the log output and the generated files in the project root

## Toolbar icon

The plugin picks the first existing icon file found in the plugin folder:
- `kicad_localizer.png`
- `icon.png`

## Limitations

- Only components that can be traced back through both schematic and PCB are fully localized
- Missing footprints or STEP files are skipped with a warning
- Only STEP/STP models are copied to `localLibs/3d/`

## License
MIT
