import pcbnew
import shutil
import os
import re
from pathlib import Path


LOCAL_LIB = "project_local"
LOCAL_DIR = "_local_kicad"


# ------------------------------------------------------------
# Resolve env vars
# ------------------------------------------------------------

def resolve_env_path(path_str):

    def repl(match):
        var = match.group(1)
        return os.environ.get(var, match.group(0))

    return re.sub(r"\$\{([^}]+)\}", repl, path_str)


# ------------------------------------------------------------
# Extract footprints
# ------------------------------------------------------------

def extract_footprints(pcb_path, pretty_dir):

    content = pcb_path.read_text(encoding="utf-8")

    matches = re.findall(r'\(footprint\s+"([^"]+)"([\s\S]*?)\n\s*\)', content)

    for full_name, body in matches:

        name = full_name.split(":")[-1]

        mod_path = pretty_dir / f"{name}.kicad_mod"

        if mod_path.exists():
            continue

        footprint_text = f'(footprint "{name}"{body}\n)\n'
        mod_path.write_text(footprint_text, encoding="utf-8")


# ------------------------------------------------------------
# Extract symbols (FIXED)
# ------------------------------------------------------------

def extract_symbols(sch_path, sym_path):

    content = sch_path.read_text(encoding="utf-8")

    start = content.find("(lib_symbols")
    if start == -1:
        print("No lib_symbols found")
        return set()

    depth = 0
    end = start

    for i in range(start, len(content)):
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
        if depth == 0:
            end = i + 1
            break

    block = content[start:end]

    # FIX: strip library prefix
    def fix_symbol_names(match):
        full = match.group(1)
        name = full.split(":")[-1]
        return f'(symbol "{name}"'

    block = re.sub(r'\(symbol\s+"([^"]+)"', fix_symbol_names, block)

    names = set(re.findall(r'\(symbol\s+"([^"]+)"', block))

    sym_lib = f"(kicad_symbol_lib (version 20211014) (generator localizer)\n{block}\n)\n"
    sym_path.write_text(sym_lib, encoding="utf-8")

    return names


# ------------------------------------------------------------
# Rewrite schematic (FIXED)
# ------------------------------------------------------------

def rewrite_schematic(sch_path):

    content = sch_path.read_text(encoding="utf-8")

    # FIX BOTH forms
    content = re.sub(
        r'\(lib_id\s+"([^"]+):([^"]+)"\)',
        lambda m: f'(lib_id "{LOCAL_LIB}:{m.group(2)}")',
        content
    )

    content = re.sub(
        r'\(symbol\s+"([^"]+):([^"]+)"',
        lambda m: f'(symbol "{LOCAL_LIB}:{m.group(2)}"',
        content
    )

    sch_path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------
# Tables
# ------------------------------------------------------------

def ensure_tables(root):

    (root / "fp-lib-table").write_text(f'''
(fp_lib_table
  (lib (name "{LOCAL_LIB}")
    (type "KiCad")
    (uri "${{KIPRJMOD}}/{LOCAL_DIR}/{LOCAL_LIB}.pretty")
  )
)
''')

    (root / "sym-lib-table").write_text(f'''
(sym_lib_table
  (lib (name "{LOCAL_LIB}")
    (type "KiCad")
    (uri "${{KIPRJMOD}}/{LOCAL_DIR}/{LOCAL_LIB}.kicad_sym")
  )
)
''')


# ------------------------------------------------------------
# Plugin
# ------------------------------------------------------------

class KiCadLocalizerPlugin(pcbnew.ActionPlugin):

    def defaults(self):
        self.name = "FULL Localizer (FIXED)"
        self.category = "Modify PCB"
        self.description = "Portable project (symbols + footprints + 3D)"
        self.show_toolbar_button = True

    def Run(self):

        board = pcbnew.GetBoard()
        root = Path(board.GetFileName()).parent
        name = Path(board.GetFileName()).stem

        sch_file = root / f"{name}.kicad_sch"

        pretty_dir = root / LOCAL_DIR / f"{LOCAL_LIB}.pretty"
        pretty_dir.mkdir(parents=True, exist_ok=True)

        local_3d = root / LOCAL_DIR / "3d"
        local_3d.mkdir(parents=True, exist_ok=True)

        sym_path = root / LOCAL_DIR / f"{LOCAL_LIB}.kicad_sym"

        print("LOCALIZING...\n")

        # footprints
        extract_footprints(root / f"{name}.kicad_pcb", pretty_dir)

        for fp in board.GetFootprints():

            n = fp.GetFPID().GetLibItemName()
            fp.SetFPID(pcbnew.LIB_ID(LOCAL_LIB, n))

            for m in fp.Models():
                old = m.m_Filename
                fn = Path(old).name
                m.m_Filename = f"${{KIPRJMOD}}/{LOCAL_DIR}/3d/{fn}"

                src = Path(resolve_env_path(old))
                if src.exists():
                    dst = local_3d / fn
                    if not dst.exists():
                        shutil.copy2(src, dst)

        pcbnew.SaveBoard(board.GetFileName(), board)

        # symbols
        if sch_file.exists():
            extract_symbols(sch_file, sym_path)
            rewrite_schematic(sch_file)

        ensure_tables(root)

        pcbnew.Refresh()

        print("\nDONE")


KiCadLocalizerPlugin().register()
