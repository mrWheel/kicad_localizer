# KiCad Localizer - Action Plugin Specification (Current Behavior)

## 1. Purpose

The plugin converts a KiCad project into a project-local layout where symbol,
footprint, and 3D dependencies are copied under the project root and references
are rewritten to those local assets.

Primary objective:
- Minimize dependency on global/system/user libraries.
- Make project transfer between machines predictable.
- Produce explicit logs for what was rewritten, skipped, or left untouched.

Important truth:
- The plugin aims for full portability.
- If symbols/footprints/3D model filenames contain invalid characters (`/`, `(`, `)`),
  those items are skipped by design and the run ends with
  `PROJECT IS NOT PORTABLE ... WITH ERRORS`.

## 2. Runtime Flags And Core Choices

Module constants currently in use:
- `LOCAL_LIB = "localLib"`
- `LOCAL_LIBS_DIR = "localLibs"`
- `MODEL_EXT_PRIORITY = [".step", ".stp", ".wrl"]`
- `REWRITE_SCHEMATIC_REFS = True`
- `SYNC_SCHEMATIC_LIB_SYMBOLS = True`

Behavioral choices:
- Multi-sheet projects are supported (`*.kicad_sch` in project root, with main
  sheet first).
- Unsafe names are skipped and logged as `ERROR` (not auto-renamed in schematic
  refs where that may break KiCad internals).
- `lib_symbols` cache is synchronized from generated local symbol library after
  rewrite, to prevent `lib_id`/cache mismatches.
- 3D model resolution is footprint-first to avoid symbol-name collisions.

## 3. Output Contract

After a successful run, these artifacts are (re)generated in the project root:

- `components/<LocalSymbolName>/`
  - `<LocalSymbolName>.kicad_sym`
  - `<LocalFootprintName>.kicad_mod`
  - `<LocalSymbolName>.<step|stp|wrl>` (0..n variants, based on source)

- `localLibs/`
  - `localLib.kicad_sym`
  - `localLib.pretty/*.kicad_mod`
  - `3d/*.{step,stp,wrl}`

- `fp-lib-table`
- `sym-lib-table`

And references are rewritten in:
- all `*.kicad_sch` files in root
- `<project>.kicad_pcb`

## 4. High-Level Pipeline (Run)

The plugin executes the following ordered steps:

1. Parse project and detect all schematic sheets.
2. Parse used symbols/footprints/references from all sheets.
3. Parse board footprints and model references.
4. Build unique local naming maps.
5. Export per-component files into `components/`.
6. Build combined local symbol lib (`localLib.kicad_sym`).
7. Build combined local footprint lib (`localLib.pretty`).
8. Copy all available 3D assets into `localLibs/3d`.
9. Upsert project library tables (`fp-lib-table`, `sym-lib-table`).
10. Rewrite schematic references (`lib_id`, footprint property, symbol_instances footprint).
11. Sync schematic `lib_symbols` cache from generated local symbol library.
12. Rewrite PCB footprint IDs and 3D refs in memory.
13. Save board, then run text-level post-pass over PCB model refs.
14. Rewrite model refs inside `localLib.pretty` footprints.
15. Validate that model refs use `${KIPRJMOD}/localLibs/3d/`.
16. Emit summary and portability verdict.

## 5. Parsing And Data Model

Schematic parsing produces:
- `components`: per symbol name metadata
- `footprint_to_component`
- `reference_to_component`

The parser reads both:
- normal `(symbol ...)` blocks
- `(symbol_instances)/(path ...)` fallback data

This is critical because many KiCad projects store useful ref/footprint data in
`symbol_instances`, not only in symbol blocks.

Board parsing produces:
- footprint map from `.kicad_pcb` blocks
- footprint->model candidate map
- component->model candidates from reference mapping
- component->footprint fallback mapping

## 6. Name And Safety Rules

Name utilities:
- `toSafeLocalItemName`: sanitizes generic filesystem-hostile chars but keeps `-`.
- `makeUniqueLocalItemName`: guarantees uniqueness with numeric suffixes.

Portability safety gate:
- `hasUnsafeLocalItemNameChars` checks for `/`, `(`, `)`.
- If detected in symbol name, footprint name, or model stem:
  - object is skipped
  - clear `ERROR` log is emitted
  - skip counters are incremented

Rationale:
- These chars are known to break local filename / KiCad item consistency in
  this workflow.

## 7. 3D Model Selection Strategy

Model source priority during component export:
1. footprint-based model (`model_paths[footprint_name]`) - preferred
2. component mapped from board reference (`component_model_map[component_name]`)
3. fallback by component name in model map
4. resolve from footprint library (`FootprintLoad`)
5. resolve from footprint block `(model "...")`

Variants:
- sibling `.step/.stp/.wrl` files are collected when available
- preferred extension order: `.step` > `.stp` > `.wrl`

Why footprint-first:
- A single symbol can be used with different footprints.
- Footprint-first avoids wrong 3D assignments caused by symbol-name reuse.

PCB rewrite model choice:
- reads preferred model stem/ext from `localLib.pretty/<footprint>.kicad_mod`
- if available in `localLibs/3d`, this choice overrides generic fallbacks
- logs one line per unique mapping:
  - `footprint <name> -> model <stem><ext>`

## 8. Schematic Rewrite Policy

Rewrites across all schematic sheets:
- `(lib_id "X:Y")` -> `(lib_id "localLib:<mapped>")` where mapped exists
- `(property "Footprint" "X:Y")` -> localLib footprint mapping where known
- `(footprint "X:Y")` in `symbol_instances/path` -> localLib footprint mapping

Power symbols are preserved (`power:*`).

`lib_symbols` handling:
- direct mutation of in-place cache names/properties is intentionally avoided
  in `rewriteSchematic` to reduce risk of unit-prefix corruption.
- instead, cache sync is executed from generated `localLib.kicad_sym`.

## 9. `lib_symbols` Cache Sync Strategy

With `SYNC_SCHEMATIC_LIB_SYMBOLS = True`:
- read generated `localLib.kicad_sym`
- preserve existing schematic cache entries not owned by exported symbols
  (for example power symbols)
- replace exported symbol cache entries with localLib-prefixed blocks
- apply to every schematic sheet

This keeps schematic cache consistent with rewritten `lib_id` references.

## 10. PCB Rewrite Strategy

In-memory rewrite via pcbnew API:
- footprint library nick -> `localLib`
- model path -> `${KIPRJMOD}/localLibs/3d/<model>`

Then text post-pass on saved PCB:
- normalizes remaining model refs by stem/ext matching local 3D directory
- records library transition info for diagnostics

`localLib.pretty` post-pass:
- rewrites each `(model "...")` to local KIPRJMOD path
- chooses best matching local model by stem and extension priority

## 11. Library Table Rules

`ensureTables` upserts exactly one `localLib` entry in each table:
- `fp-lib-table` -> `${KIPRJMOD}/localLibs/localLib.pretty`
- `sym-lib-table` -> `${KIPRJMOD}/localLibs/localLib.kicad_sym`

No destructive cleanup of unrelated table entries is required for correctness.

## 12. Logging Contract

The plugin provides step logs and structured counters:
- component/footprint/model discovery counts
- export summary
- rewrite counters for schematic and board
- model transition summaries
- model path validation results
- explicit per-footprint model mapping lines

Final verdict:
- If skip counters are zero:
  - `DONE (components + localLibs/)`
- If any unsafe object was skipped:
  - `PROJECT IS NOT PORTABLE: [N] Symbols, [M] Footprints, [P] 3D models ...`
  - `DONE (components + localLibs/) WITH ERRORS`

## 13. Definition Of Portable Project (For This Plugin)

A project is considered portable by this plugin when:
- schematic symbol refs point to `localLib:*` (except intentionally preserved power)
- schematic footprint refs point to `localLib:*` where applicable
- schematic `lib_symbols` cache is synchronized with local symbol library
- pcb footprints use `localLib:*`
- pcb and local footprint model refs resolve under `${KIPRJMOD}/localLibs/3d/`
- no unsafe-name skips occurred

## 14. Known Non-Portable Triggers

Run will be explicitly marked non-portable when any of these occurs:
- symbol name contains `/` or parentheses
- footprint name contains `/` or parentheses
- 3D model filename stem contains `/` or parentheses

These are hard-skip rules today.

## 15. Practical Runbook For Reliable Results

Recommended operator flow:
1. Start from a clean project copy (not previously localized).
2. Run plugin once.
3. Check log for `PROJECT IS NOT PORTABLE` and any skip lines.
4. Check per-footprint model mapping lines for suspicious assignments.
5. Open schematic editor and PCB editor.
6. Verify `Assign Footprints` uses `localLib:*` refs.
7. Verify 3D viewer for critical footprints.

If the run logs portability errors, the project is intentionally not declared
fully portable until those source naming issues are corrected.

## 16. Change Control Requirement

This document must track actual implementation in:
- `kicad_localizer/__init__.py`

When behavior changes (especially rewrite order, safety rules, model selection,
cache sync), update this specification in the same change set.
