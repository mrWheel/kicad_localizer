# KiCad Action Plugin Specification

## Goal
Create a tool (Python script or KiCad Action Plugin) that:
- Extracts all used components from a KiCad project
- Stores them in a per-component folder structure
- Rebuilds a project-local symbol and footprint library

## code conduct
Use:
- allman style 
- lowerCamelCase naming
- two spaces indent where possible

---

## Output Structure

project_root/
  components/
    <component_name>/
      <component_name>.kicad_sym
      <component_name>.kicad_mod
      <component_name>.step

  localLibs/
    localLib.kicad_sym
    localLib.pretty/
      <component_name>.kicad_mod
    3d/
      <component_name>.step

---

## Functional Requirements

### 1. Input Detection
- Detect project root (.kicad_pro)
- Locate .kicad_sch and .kicad_pcb

### 2. Extract Used Symbols
Parse schematic for:
(lib_id "Library:Symbol")

### 3. Resolve Symbol Libraries
- Search global and project symbol libraries
- Load corresponding .kicad_sym

### 4. Extract Symbol Definitions
- Full (symbol ...) block
- Include properties, units, aliases, inheritance

### 5. Create Component Folders
components/<SymbolName>/

### 6. Write Symbol File
components/<name>/<name>.kicad_sym

### 7. Extract Footprint Reference
(property "Footprint" "Library:Footprint")

### 8. Resolve Footprints
Locate .kicad_mod in footprint libraries

### 9. Copy Footprints
- components/<name>/<name>.kicad_mod
- localLibs/localLib.pretty/<name>.kicad_mod

### 10. Extract 3D Models
Parse footprint for (model "...")

### 11. Copy STEP Files
- components/<name>/<name>.step
- localLibs/3d/<name>.step

### 12. Rewrite 3D Paths
${KIPRJMOD}/localLibs/3d/<name>.step

### 13. Build Combined Symbol Library
localLibs/localLib.kicad_sym

### 14. Build Footprint Library
localLibs/localLib.pretty/

### 15. Deduplication
- Avoid duplicates
- Handle naming conflicts

### 16. Logging
Info, Warning, Error messages

### 17. Edge Cases
- Multi-unit symbols
- Aliases
- Inheritance
- Multiple libraries

### 18. CLI Interface
python kicad_extract.py --project <path>

### 19. Idempotent
Repeatable results

### 20. Non-destructive
Do not modify original libraries

---

## Optional Features
- Git-friendly ordering
- Hash-based change detection
- CI support
- JSON report
