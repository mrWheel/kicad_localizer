# KiCad Action Plugin Specification

## Goal

Create a KiCad Action Plugin (`pcbnew.ActionPlugin`) that makes a KiCad project
fully self-contained by:

- Extracting every used component (symbol, footprint, 3D model) from global/user
  libraries into a per-component folder structure under `components/`.
- Assembling project-local combined libraries under `localLibs/`.
- Rewriting all library references in the schematic and PCB to point exclusively
  to the local libraries.
- Validating and logging every 3D-model reference rewrite.

After a successful run the project compiles and opens without depending on any
external library path.

---

## Code Style

- **Indentation**: two spaces throughout (including nested blocks).
- **Naming**: `lowerCamelCase` for all functions and local variables.
- **Allman-style bracing** where applicable.
- **Error handling**: bare `except Exception` is acceptable inside helper
  functions that must not crash the plugin; always `continue` or `return None`
  silently rather than propagating.

---

## Constants

```python
LOCAL_LIB      = "localLib"          # library nickname used everywhere
LOCAL_LIBS_DIR = "localLibs"         # subdirectory that holds combined libs
MODEL_EXT_PRIORITY = [".step", ".stp", ".wrl"]  # preferred order for 3D models
```

---

## Output Structure

```
project_root/
  components/
    <ComponentName>/
      <ComponentName>.kicad_sym    # symbol definition, wrapped in kicad_symbol_lib header
      <ComponentName>.kicad_mod    # footprint definition, name-normalized
      <ComponentName>.<ext>        # 3D model; extension matches source (.step/.stp/.wrl)

  localLibs/
    localLib.kicad_sym             # combined symbol library
    localLib.pretty/
      <ComponentName>.kicad_mod    # copy from components/
    3d/
      <ComponentName>.<ext>        # copy from components/; extension preserved

  fp-lib-table                     # upserted to include localLib entry
  sym-lib-table                    # upserted to include localLib entry
```

The schematic (`.kicad_sch`) and PCB (`.kicad_pcb`) files are rewritten in-place.
Original global libraries are never modified.

---

## Environment Variable Resolution

All `${VAR}` and `$(VAR)` references in model paths must be resolved using a
four-layer lookup built in `getPathVars(project_root=None)`:

1. **OS environment** — `os.environ` as baseline.
2. **KiCad common config** (`loadKicadCommonPathVars()`):
   - Scan `~/Library/Preferences/kicad/*/kicad_common.json` (macOS) and
     `~/.config/kicad/*/kicad_common.json` (Linux/Windows).
   - Sort candidates by version number (`parseVersionKey()`), take only the
     highest version.
   - Read `data["environment"]["vars"]` (a `dict[str, str]`).
   - This exposes user-defined variables such as `MY3DMODS`, `MYNEWLIBRARY`, etc.
3. **KiCad installation discovery** (`discoverKicadInstallPathVars()`):
   - Scan `/Applications/KiCad-*/KiCad-*.app/Contents/SharedSupport` and
     `/Applications/KiCad/KiCad.app/Contents/SharedSupport`.
   - For each found `SharedSupport/3dmodels` directory, extract the major version
     number from the path using regex `KiCad-(\d+)`.
   - Register `KICAD{major}_3DMODEL_DIR` → path (e.g. `KICAD9_3DMODEL_DIR`).
   - Also register `KISYS3DMOD` → same path (first found wins for both).
   - Use `setdefault` so the first found install wins.
4. **Project text variables** (`loadProjectTextVars(project_root)`):
   - Find the first `*.kicad_pro` in `project_root`.
   - Read `data["text_variables"]` (a `dict[str, str]`).

The combined dict is cached per `project_root` in `_PATH_VARS_CACHE` (module-level
dict, key = `str(project_root)` or `"GLOBAL"`).

`resolveEnvPath(path_str, project_root=None)` applies the lookup twice with
`re.sub`: first `\$\{([^}]+)\}` then `\$\(([^)]+)\)`. Unresolved variables are
left unchanged (match returned as-is).

---

## S-expression Utilities

All KiCad file formats use S-expressions. Implement the following pure-text
utilities (no external parser):

### `findBalancedBlock(content, start_index) -> str | None`
Walk `content` from `start_index`, tracking parenthesis depth. Return the
substring from `start_index` to the closing `)` (inclusive) when depth reaches
zero. Return `None` if unbalanced.

### `findBlockByToken(content, token) -> str | None`
Find the first occurrence of `token` in `content`, then call
`findBalancedBlock` at that position.

### `extractBlocksByToken(content, token) -> list[str]`
Find all non-overlapping occurrences of `token`, return each as a balanced block.

### `extractSymbolBlocks(content) -> list[str]`
Find all `(symbol ...)` blocks. Use `re.finditer(r'\(symbol(?=[\s\)])', content)`
to avoid false matches on `(symbol_instances ...)`. Return each as a balanced
block.

### `extractDirectChildBlocks(root_block, token) -> list[str]`
Walk `root_block` tracking depth. At depth 1, whenever a `(` is followed
immediately by `token`, extract that balanced block and skip past it.

### `splitTopLevelSymbols(lib_symbols_block) -> list[str]`
Strip the outer `(lib_symbols … )` wrapper. Scan for `(symbol ` markers and
return each as a balanced block.

---

## Name-Normalization Utilities

### `getSymbolBlockName(symbol_block) -> str | None`
Extract the quoted name from `(symbol "…"` using regex; return the name string.

### `prefixSymbolBlockName(symbol_block, library_name) -> str`
If the symbol name does not already contain `:`, prefix it as
`{library_name}:{name}`. Replace only the first `(symbol "…"` occurrence.

### `normalizeSymbolBlockName(symbol_block, symbol_name) -> str`
Strip library prefix from every `(symbol "Lib:Name"` occurrence in the block
(replace with bare `Name`), then ensure the top-level declaration matches
`symbol_name` exactly. Replace all occurrences for inner declarations,
`count=1` for the top-level.

### `normalizeFootprintBlockName(footprint_block, footprint_name) -> str`
Replace the first `(footprint "…"` with `(footprint "{footprint_name}"`.

### `rewriteSymbolFootprintProperty(symbol_block, footprint_ref) -> str`
Replace the value of the `Footprint` property inside a symbol block.
If no `Footprint` property exists, inject a new one with standard `(at 0 0 0)`,
`(effects (font (size 1.27 1.27)) (hide yes))` attributes before the closing `)`.

### `splitFootprintReference(footprint_ref) -> (lib_nick, item_name)`
Split `"Lib:Name"` on the first `:`. Return `("", footprint_ref)` if no `:`.

---

## Parsing Functions

### `parseUsedComponents(sch_path) -> (content, components, footprint_to_component, reference_to_component)`

Parse the schematic file text. For each `(symbol …)` block that contains
`(lib_id "…")`:

- Extract `symbol_name` = last colon-split segment of the lib_id value.
- Extract `footprint` = last colon-split segment of value of
  `(property "Footprint" "…")`.
- Extract `footprint_full` = the raw full `Lib:Footprint` value (unsplit).
- Extract `reference` = value of `(property "Reference" "…")`.
- Extract `uuid` = value of `(uuid "…")`.

Build:
- `components: dict[symbol_name → {symbol, footprint, footprint_full}]` — one
  entry per unique symbol name; if multiple instances exist, fill in missing
  footprint from the first instance that has one.
- `footprint_to_component: dict[footprint_name → symbol_name]`
- `reference_to_component: dict[reference → symbol_name]`
- `uuid_to_component: dict[uuid → symbol_name]` (internal use)

After the main loop, scan `(symbol_instances …)` → `(path …)` blocks using
`uuid_to_component` to fill in references and footprints that KiCad stores there
instead of in the symbol blocks themselves. Update `reference_to_component` and
`footprint_to_component` accordingly.

Fallback: if `components` is still empty, collect lib_ids by regex only.

### `extractSymbolDefinitionMap(sch_content) -> dict[symbol_name → block]`

Find `(lib_symbols …)` in the schematic content. Split into top-level symbol
blocks with `splitTopLevelSymbols`. For each block extract the full name
(which may be `Lib:Name`), use the bare `Name` as the key, and store the block
after `normalizeSymbolBlockName`.

### `extractFootprintMap(pcb_path) -> dict[footprint_name → block]`

Read PCB file. Extract all `(footprint …)` blocks via
`extractBlocksByToken`. For each, key = bare name (last colon-segment),
value = normalized block. Skip duplicates (first wins).

---

## 3D Model Collection

### `pickPreferredModelPath(model_candidates) -> Path | None`
Build a `dict[ext → Path]` from candidates. Return the first match in
`MODEL_EXT_PRIORITY`. If none match, return the first candidate or `None`.

### `collectFootprintModelPaths(board) -> dict[footprint_name → Path]`
Iterate `board.GetFootprints()`. For each footprint, resolve every
`model.m_Filename` with `resolveEnvPath(…, pcb_dir)`. Collect candidates whose
suffix is in `MODEL_EXT_PRIORITY` and that exist on disk. Store the best
candidate per footprint name (first wins). Uses PCB file directory as
`project_root`.

### `resolveModelFromFootprintLibrary(footprint_ref, project_root) -> Path | None`
Call `pcbnew.FootprintLoad(lib_nick, item_name)`. Resolve model paths in the
loaded footprint using `resolveEnvPath`. Return the best candidate or `None`.
Silently catch all exceptions.

### `mapComponentModelsFromBoard(board, reference_to_component) -> dict[component_name → Path]`
Iterate `board.GetFootprints()`. Look up each footprint's reference in
`reference_to_component` to get the component name. Resolve model paths (no
project_root — uses global vars only). Return the best model per component name.

### `countLocalizedModelRefs(board) -> int`
Count model references already pointing to `${KIPRJMOD}/localLibs/3d/`.

### `mapComponentFootprintsFromBoard(board, reference_to_component, components) -> dict[component_name → footprint_name]`
Iterate board footprints. Map component name → footprint name via
`reference_to_component`. Fallback: for components with no mapping, if
the component name itself appears as a board footprint name, map it to itself.

---

## Component Export

### `exportComponentFiles(root, components, symbol_map, footprint_map, model_paths, component_model_map, component_to_footprint_fallback, logger) -> (exported, stats)`

For each component in `sorted(components.keys())`:

1. Resolve `footprint_name`:
   - From `components[name]["footprint"]`.
   - Fallback: `component_to_footprint_fallback[name]` (from board scan).
2. Look up `symbol_block = symbol_map.get(component_name)`.
   - If missing: warn, increment `missing_symbol_definition`, `continue`.
3. If `footprint_name` is empty: log info ("schematic-only"), increment
   `missing_footprint_name`, `continue`.
4. Look up `footprint_block = footprint_map.get(footprint_name)`.
   - If missing, try `footprint_map.get(component_name)` (already-localized board).
   - If still missing: warn, increment `missing_footprint_definition`, `continue`.
5. Write footprint: `normalizeFootprintBlockName(block, component_name) + "\n"`.
6. Write symbol: wrap block in
   ```
   (kicad_symbol_lib (version 20211014) (generator localizer)\n  {block}\n)\n
   ```
   After calling `rewriteSymbolFootprintProperty(block, "localLib:{component_name}")`.
7. Resolve 3D model source by priority:
   a. `component_model_map.get(component_name)` (board-reference-mapped, preferred).
   b. `model_paths.get(footprint_name)`.
   c. `model_paths.get(component_name)`.
   d. `resolveModelFromFootprintLibrary(footprint_full, root)`.
8. If model found: `shutil.copy2(src, component_dir / f"{component_name}{src.suffix.lower()}")`.
9. Collect stats: `exported_symbol`, `exported_footprint`, `exported_model`,
   `step_from_fp_library`, `model_from_component_board_map`.

Return `exported: dict[name → {symbol, footprint, model, model_ext, has_model, has_footprint}]`
and `stats` dict.

---

## Library Assembly

### `buildCombinedSymbolLibrary(root, logger=None) -> Path`

Glob `components/*/*.kicad_sym`. For each file, extract the first
`(symbol "…")` block. Assemble into:
```
(kicad_symbol_lib (version 20211014) (generator localizer)
  {block}
  …
)
```
Write to `localLibs/localLib.kicad_sym`.

### `buildCombinedFootprintLibrary(root, exported_components) -> Path`

Create `localLibs/localLib.pretty/`. For each exported component that has a
footprint, copy `components/<name>/<name>.kicad_mod` → `localLib.pretty/<name>.kicad_mod`.

### `buildCombined3dDirectory(root, exported_components) -> (Path, dict[name → ext])`

Create `localLibs/3d/`. For each exported component that has a model, copy
`components/<name>/<name><ext>` → `localLibs/3d/<name><ext>`. Preserve the
actual extension. Return `(out_dir, model_ext_by_component)`.

---

## Schematic Rewriting

### `rewriteSchematic(sch_path, footprint_to_component, exported_names) -> {lib_ids, footprints}`

Rewrite the schematic in-place using two `re.sub` passes:

1. `(lib_id "Lib:Name")` → `(lib_id "localLib:Name")` — only when `Name` is in
   `exported_names`. Symbols not in `exported_names` (e.g. power symbols) are
   left untouched.
2. `(property "Footprint" "Lib:Name"` → `(property "Footprint" "localLib:ComponentName"` —
   map footprint base name through `footprint_to_component` first; rewrite only
   when the resolved component name is in `exported_names`.

Return counts of rewrites.

### `syncSchematicLibSymbolsFromLocalLibrary(root, sch_path, exported_names, logger=None) -> int`

Update the `(lib_symbols …)` cache block inside the schematic:

1. Read `localLibs/localLib.kicad_sym`.
2. Extract existing top-level blocks from schematic's `(lib_symbols …)` via
   `splitTopLevelSymbols`.
3. **Preserve** all blocks whose bare name (`split(":")[-1]`) is NOT in
   `exported_names` — this keeps power symbols (`power:GND`, etc.) intact.
4. Append blocks from localLib, each prefixed with `prefixSymbolBlockName(block, LOCAL_LIB)`.
5. Rebuild `(lib_symbols\n\t\t{block lines}\n\t)` and replace the old block in
   the schematic file.

Return count of localLib blocks written.

---

## PCB Rewriting

### `rewriteBoard(board, root, footprint_to_component, localized_names) -> int`

Rewrite in-memory (pcbnew API):

- For each footprint: map its `GetLibItemName()` through `footprint_to_component`;
  if in `localized_names`, call `fp.SetFPID(pcbnew.LIB_ID(LOCAL_LIB, component_name))`.
- For each model on that footprint: look up `component_name` in
  `model_ext_by_component` (built from `localLibs/3d/`). If found, set
  `model.m_Filename = "${KIPRJMOD}/localLibs/3d/{name}{ext}"`.
  Fallback: if the component name has no model, try matching the model's basename
  stem against `model_ext_by_component`.
- Try `models[i].m_Filename = new_ref` first; if that raises, update via the
  model object and reassign `models[i]`.

Return count of changed model entries. Call `board.Save(str(pcb_path))` in the
`Run()` method after this.

### `rewriteBoardModelPathsInFile(pcb_path, root) -> (changed_lines, transitions)`

After `board.Save`, perform a second text-level pass on the saved PCB file.

For each file in `localLibs/3d/`:
- Build regex `\(model\s+"([^"]*/{stem}\.(?:step|stp|wrl))"`.
- Replace with `(model "${KIPRJMOD}/localLibs/3d/{stem}{ext}"`.
- Record `transitions: dict[component_name → set((old_lib, new_lib))]`.

Write file only if changes were made.

### `rewritePrettyModelPaths(root) -> (changed_lines, transitions)`

For every `.kicad_mod` in `localLibs/localLib.pretty/`:
- Find matching model in `localLibs/3d/` by stem and `MODEL_EXT_PRIORITY`.
- Replace all `(model "…")` references with
  `(model "${KIPRJMOD}/localLibs/3d/{stem}{ext}")`.
- Write file only when content changed.

---

## Library Table Management

### `upsertProjectLibTable(table_path, table_type, name, uri)`

Manage `fp-lib-table` / `sym-lib-table` S-expression files.

- If the file does not exist, create `({root_token}\n)\n`.
- If an entry with `(name "{name}")` already exists, replace it using `re.sub`
  matching `(lib (name "…") (type "…") (uri "…") )`.
- Otherwise, insert the new entry before the final `)` of the file.

Entry format:
```
  (lib (name "{name}")
    (type "KiCad")
    (uri "{uri}")
  )
```

### `ensureTables(root)`

Call `upsertProjectLibTable` for both tables:
- `fp-lib-table` → `${KIPRJMOD}/localLibs/localLib.pretty`
- `sym-lib-table` → `${KIPRJMOD}/localLibs/localLib.kicad_sym`

---

## Logging

### `RuntimeLogger(title)`

On construction, if `wx` is available and a `wx.App` is running, open a
`wx.Frame` (860×520) containing a multiline read-only `wx.TextCtrl` with
`wx.TE_RICH2`. Call `frame.Show()`, `frame.Raise()`, `wx.YieldIfNeeded()`.

Methods `info(msg)`, `warn(msg)`, `error(msg)` each prepend `[INFO]`, `[WARN]`,
`[ERROR]`, print to stdout, and append to the text control (with
`frame.Update()` and `wx.YieldIfNeeded()` after each write).

If `wx` is not importable (headless/test), import it as `None` and log to stdout
only.

### `logLibraryTransitions(logger, title, transitions)`

Log `title`, then for each component in sorted order log
`  {component}: {old_lib} -> {new_lib}`.

### `logModelRefSummary(logger, root, pcb_path, phase)`

Parse model refs from PCB and every `.kicad_mod` in `localLib.pretty`. Log
counts: `total`, `non_kiprjmod`, `localLibs`. Log up to 3 bad refs as warnings.
The `phase` label is `"before"` or `"after"`.

### `logNonKiprjmod3dErrors(logger, root, pcb_path)`

After rewriting, scan the PCB and every `.kicad_mod` in `localLib.pretty` for
any `(model "…")` reference that does not start with
`${KIPRJMOD}/localLibs/3d/`. Log `[ERROR]` with counts and up to 5 offending
references. If all references are correct, log `[INFO] OK`.

---

## Plugin Entry Point

### `KiCadLocalizerPlugin(pcbnew.ActionPlugin)`

`defaults()`:
- `name = "KiCad Localizer"`
- `category = "Modify PCB"`
- `description = "Export per-component symbol, footprint, and step files"`
- `show_toolbar_button = True`
- Set `icon_file_name` to `kicad_localizer.png` or `icon.png` if either
  exists in the plugin directory.

`Run()` — eight ordered steps:

1. **Parse**: resolve `board`, `root`, `project_name`, `sch_path`, `pcb_path`.
   Abort with `[ERROR]` if either file is missing.
   Call `parseUsedComponents`, `extractSymbolDefinitionMap`,
   `extractFootprintMap`, `collectFootprintModelPaths`,
   `mapComponentModelsFromBoard`, `countLocalizedModelRefs`,
   `mapComponentFootprintsFromBoard`.
   Log counts for each. Abort if no components found.
   Emit `[ERROR]` (but do not abort) if no model paths were resolved but the
   board already has localized 3D refs — indicates the source project has already
   been localized and lost its original paths.

2. **Export**: call `exportComponentFiles`. Log export summary and missing
   summary. Abort if `exported` is empty.

3. **Build symbol library**: `buildCombinedSymbolLibrary`.

4. **Build footprint library**: `buildCombinedFootprintLibrary`.

5. **Build 3D directory**: `buildCombined3dDirectory`.

6. **Write library tables**: `ensureTables`.

7. **Rewrite schematic**: `rewriteSchematic` then
   `syncSchematicLibSymbolsFromLocalLibrary`.

8. **Rewrite PCB**: `logModelRefSummary(…, "before")` → `rewriteBoard` →
   `board.Save(pcb_path)` → `rewritePrettyModelPaths` →
   `rewriteBoardModelPathsInFile` → log transitions →
   `logModelRefSummary(…, "after")` → `logNonKiprjmod3dErrors`.

Log final summary line:
```
SUMMARY symbols={n} footprints={n} models={n} pretty3d={n} pcb3d={n}
```

Call `pcbnew.Refresh()`. Log `DONE (components + localLibs/)`.

Register with: `KiCadLocalizerPlugin().register()`

---

## Behavioral Contracts

- **Idempotent**: running the plugin multiple times produces the same result.
  Footprint names on an already-localized board are matched via the component
  name fallback in `exportComponentFiles`. `upsertProjectLibTable` replaces
  rather than duplicates entries.
- **Non-destructive**: only `components/`, `localLibs/`, the schematic, the PCB,
  `fp-lib-table`, and `sym-lib-table` are written. Global library files are never
  modified.
- **Power/non-exported symbols preserved**: `rewriteSchematic` and
  `syncSchematicLibSymbolsFromLocalLibrary` both skip symbols whose name is
  not in `exported_names`, so `power:GND`, `power:VCC`, etc. remain unchanged.
- **3D extension-agnostic**: model file extension (`.step`, `.stp`, `.wrl`) is
  discovered at runtime and preserved consistently through all rewrite passes.
- **Headless-safe**: the plugin works without a running wx application; all wx
  calls are guarded by `if wx is None` or `if self.frame is not None`.
