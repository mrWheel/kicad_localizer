# KiCad Localizer Plugin

## Overview

KiCad Localizer is a KiCad Action Plugin that rewrites a project to use
project-local symbol, footprint, and 3D assets.

Goal:
- Make projects transferable between machines without relying on external
  library paths.
- Keep all relevant dependencies under the project root.
- Provide explicit logs for every major rewrite and fallback decision.

## What the plugin generates

```text
project_root/
  components/
    <LocalSymbolName>/
      <LocalSymbolName>.kicad_sym
      <LocalFootprintName>.kicad_mod
      <LocalSymbolName>.<step|stp|wrl>

  localLibs/
    localLib.kicad_sym
    localLib.pretty/
      *.kicad_mod
    3d/
      *.{step,stp,wrl}

  fp-lib-table
  sym-lib-table
```

The plugin also rewrites all project-root schematic sheets (`*.kicad_sch`) and
the project PCB file (`<project>.kicad_pcb`).

## Current behavior (important)

Runtime choices currently enabled:
- `REWRITE_SCHEMATIC_REFS = True`
- `SYNC_SCHEMATIC_LIB_SYMBOLS = True`

This means:
- Schematic references are rewritten to `localLib:*` where possible.
- Schematic `lib_symbols` cache is synchronized from generated
  `localLibs/localLib.kicad_sym`.
- Multi-sheet projects are supported (all `*.kicad_sch` in root).

## Rewrite scope

### Schematic

Across all sheets, the plugin rewrites:
- `(lib_id "Lib:Name")` -> `(lib_id "localLib:MappedName")` where mapped.
- `(property "Footprint" "Lib:Name")` -> `(property "Footprint" "localLib:MappedFootprint")` where mapped.
- `(footprint "Lib:Name")` in `symbol_instances/path` blocks, so Assign
  Footprints shows `localLib:*`.

Power symbols (`power:*`) are intentionally left unchanged.

### PCB

The plugin rewrites:
- Footprint links to `localLib:<mapped footprint>`.
- 3D model refs to `${KIPRJMOD}/localLibs/3d/<model>.<ext>`.

3D rewriting is done in two passes:
1. In-memory via pcbnew API.
2. Text post-pass after board save for remaining refs.

### localLib.pretty

Model refs inside localized footprints are rewritten to
`${KIPRJMOD}/localLibs/3d/...`.

## 3D model selection strategy

Model source priority during export:
1. Footprint-based model mapping from board footprint models.
2. Component mapping via board reference.
3. Fallback by component name.
4. Resolve from footprint library (`FootprintLoad`).
5. Resolve from footprint block `(model "...")`.

Preferred extensions: `.step` -> `.stp` -> `.wrl`.

Why footprint-first:
- The same symbol name can be used with different footprints.
- Footprint-first avoids model mix-ups between those variants.

During board rewrite the plugin logs explicit mapping lines, for example:
- `footprint RJ12_Amphenol_54601 -> model RJ12_6c6p.step`

## Portability and safety rules

The plugin has hard skip rules for unsafe names containing `/`, `(`, `)`:
- symbol names
- footprint names
- 3D model filename stems

When encountered:
- object is skipped
- an `ERROR` log is emitted
- counters are added to final portability verdict

Final verdict:
- Success path: `DONE (components + localLibs/)`
- With skipped unsafe items:
  - `PROJECT IS NOT PORTABLE: [N] Symbols, [M] Footprints, [P] 3D models ...`
  - `DONE (components + localLibs/) WITH ERRORS`

## Installation

Copy this repository folder to KiCad scripting plugins and restart KiCad.

Common plugin locations:
- macOS: `~/Documents/KiCad/9.0/scripting/plugins`
- Linux: `~/.local/share/kicad/9.0/scripting/plugins`
- Windows: `%APPDATA%\kicad\9.0\scripting\plugins`

The plugin class is registered from `kicad_localizer/__init__.py`.

## Usage

1. Open the project in KiCad.
2. Open PCB Editor.
3. Run Action Plugin: KiCad Localizer.
4. Inspect runtime log.
5. Verify:
   - schematic opens (including Symbol Editor interactions)
   - Assign Footprints shows `localLib:*`
   - PCB footprints point to `localLib:*`
   - 3D refs use `${KIPRJMOD}/localLibs/3d/`

## Recommended run workflow

For deterministic results:
1. Start from a clean project copy (not already localized).
2. Run plugin once.
3. Review skip/error lines and final verdict.
4. If needed, inspect per-footprint model mapping lines.

## Limitations

- If source design data contains unsafe names (`/`, parentheses), those objects
  are skipped and the run is explicitly marked non-portable.
- The plugin focuses on project-root schematic sheets (`*.kicad_sch` in root).
- Existing unusual/manual custom edits in KiCad text files may still require
  project-specific verification after rewrite.

## Related docs

Detailed implementation spec:
- `actionPlugin.md`

## License

MIT
