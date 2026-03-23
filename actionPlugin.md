# KiCad Action Plugin Specification

## Goal
Create a KiCad Action Plugin that:
- Extracts all used components from a KiCad project
- Stores them in a per-component folder structure
- Rebuilds project-local symbol, footprint and 3D outputs
- Rewrites references in schematic and PCB to localLib/localLibs
- Logs and validates rewritten 3D references

## Code Conduct
Use:
- allman style 
- lowerCamelCase naming
- two spaces indent where possible

---

## Output Structure
```
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
```
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
```components/<SymbolName>/```

### 6. Write Symbol File
```components/<name>/<name>.kicad_sym```

### 7. Extract Footprint Reference
(property "Footprint" "Library:Footprint")

### 8. Resolve Footprints
Locate .kicad_mod in footprint libraries

### 9. Copy Footprints
```
- components/<name>/<name>.kicad_mod
- localLibs/localLib.pretty/<name>.kicad_mod
```

### 10. Extract 3D Models
Parse footprint for (model "...")

### 11. Copy STEP Files
```
- components/<name>/<name>.step
- localLibs/3d/<name>.step
```

### 12. Rewrite 3D Paths
```${KIPRJMOD}/localLibs/3d/<name>.step```

### 13. Build Combined Symbol Library
```localLibs/localLib.kicad_sym```

### 14. Build Footprint Library
```localLibs/localLib.pretty/```

### 15. Rewrite Schematic References
- Rewrite lib_id for exported components to ```localLib:<name>```
- Rewrite Footprint property for exported components to ```localLib:<name>```

### 16. Rewrite PCB References
- Rewrite footprint link to ```localLib:<name>```
- Rewrite 3D model refs to ```${KIPRJMOD}/localLibs/3d/<name>.step```

### 17. Validate 3D References
- Emit ERROR when any model reference in PCB or localLib.pretty does not use 
```${KIPRJMOD}/localLibs/3d/```

### 18. Transition Logging
- Log transitions per component in format:
  ```<Component>: <OldLib> -> <NewLib>```

### 19. Summary Logging
- Single line summary with totals for:
  symbols, footprints, steps, pretty3d, pcb3d

### 20. Deduplication
- Avoid duplicates
- Handle naming conflicts

### 21. Logging
Info, Warning, Error messages

### 22. Edge Cases
- Multi-unit symbols
- Aliases
- Inheritance
- Multiple libraries

### 23. Idempotent
Repeatable results

### 24. Non-destructive
Do not modify original libraries

---

## Optional Features
- Git-friendly ordering
- Hash-based change detection
- CI support
- JSON report
