import os
import re
import shutil
from pathlib import Path

import pcbnew

try:
  import wx
except Exception:
  wx = None


LOCAL_LIB = "localLib"
LOCAL_LIBS_DIR = "localLibs"


class RuntimeLogger:
  def __init__(self, title):
    self.frame = None
    self.text = None

    if wx is None:
      return

    app = wx.GetApp()
    if app is None:
      return

    self.frame = wx.Frame(None, title=title, size=(860, 520))
    panel = wx.Panel(self.frame)
    sizer = wx.BoxSizer(wx.VERTICAL)

    self.text = wx.TextCtrl(
      panel,
      style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
    )

    sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, 8)
    panel.SetSizer(sizer)

    self.frame.Show()
    self.frame.Raise()
    wx.YieldIfNeeded()

  def _write(self, level, message):
    line = f"[{level}] {message}"
    print(line)

    if self.text is not None:
      self.text.AppendText(line + "\n")
      if self.frame is not None:
        self.frame.Update()
      wx.YieldIfNeeded()

  def info(self, message):
    self._write("INFO", message)

  def warn(self, message):
    self._write("WARN", message)

  def error(self, message):
    self._write("ERROR", message)


def resolve_env_path(path_str):
  def repl_brace(match):
    var = match.group(1)
    return os.environ.get(var, match.group(0))

  def repl_paren(match):
    var = match.group(1)
    return os.environ.get(var, match.group(0))

  resolved = re.sub(r"\$\{([^}]+)\}", repl_brace, path_str)
  return re.sub(r"\$\(([^)]+)\)", repl_paren, resolved)


def find_balanced_block(content, start_index):
  depth = 0
  for i in range(start_index, len(content)):
    ch = content[i]
    if ch == "(":
      depth += 1
    elif ch == ")":
      depth -= 1
      if depth == 0:
        return content[start_index:i + 1]
  return None


def find_block_by_token(content, token):
  start = content.find(token)
  if start == -1:
    return None
  return find_balanced_block(content, start)


def extract_blocks_by_token(content, token):
  blocks = []
  idx = 0

  while True:
    start = content.find(token, idx)
    if start == -1:
      break

    block = find_balanced_block(content, start)
    if block is None:
      break

    blocks.append(block)
    idx = start + len(block)

  return blocks


def extract_symbol_blocks(content):
  blocks = []

  # Match only real '(symbol ...' openings and avoid '(symbol_instances ...'.
  for match in re.finditer(r'\(symbol(?=[\s\)])', content):
    start = match.start()
    block = find_balanced_block(content, start)
    if block is None:
      continue
    blocks.append(block)

  return blocks


def split_top_level_symbols(lib_symbols_block):
  body = lib_symbols_block[len("(lib_symbols"):]
  if body.endswith(")"):
    body = body[:-1]

  symbols = []
  i = 0

  while i < len(body):
    start = body.find("(symbol ", i)
    if start == -1:
      break

    block = find_balanced_block(body, start)
    if block is None:
      break

    symbols.append(block)
    i = start + len(block)

  return symbols


def normalize_symbol_block_name(symbol_block, symbol_name):
  return re.sub(
    r'\(symbol\s+"([^"]+)"',
    f'(symbol "{symbol_name}"',
    symbol_block,
    count=1,
  )


def normalize_footprint_block_name(footprint_block, footprint_name):
  return re.sub(
    r'\(footprint\s+"([^"]+)"',
    f'(footprint "{footprint_name}"',
    footprint_block,
    count=1,
  )


def rewrite_symbol_footprint_property(symbol_block, footprint_ref):
  if not footprint_ref:
    return symbol_block

  if re.search(r'\(property\s+"Footprint"\s+"[^"]*"', symbol_block):
    return re.sub(
      r'(\(property\s+"Footprint"\s+")([^"]*)(")',
      lambda m: f'{m.group(1)}{footprint_ref}{m.group(3)}',
      symbol_block,
      count=1,
    )

  insert_text = (
    '\n\t\t(property "Footprint" "' + footprint_ref + '"\n'
    '\t\t\t(at 0 0 0)\n'
    '\t\t\t(effects\n'
    '\t\t\t\t(font\n'
    '\t\t\t\t\t(size 1.27 1.27)\n'
    '\t\t\t\t)\n'
    '\t\t\t\t(hide yes)\n'
    '\t\t\t)\n'
    '\t\t)'
  )

  return symbol_block[:-1] + insert_text + '\n\t)' if symbol_block.endswith(')') else symbol_block


def parse_used_components(sch_path):
  content = sch_path.read_text(encoding="utf-8")
  symbol_blocks = extract_symbol_blocks(content)

  components = {}
  footprint_to_component = {}
  reference_to_component = {}
  uuid_to_component = {}

  for block in symbol_blocks:
    if "(lib_id \"" not in block:
      continue

    lib_match = re.search(r'\(lib_id\s+"([^"]+)"\)', block)
    if not lib_match:
      continue

    full_symbol = lib_match.group(1)
    symbol_name = full_symbol.split(":")[-1]
    uuid_match = re.search(r'\(uuid\s+"([^"]+)"\)', block)

    footprint_match = re.search(
      r'\(property\s+"Footprint"\s+"([^"]*)"',
      block,
    )
    reference_match = re.search(
      r'\(property\s+"Reference"\s+"([^"]+)"',
      block,
    )
    footprint_name = ""
    if footprint_match:
      footprint_value = footprint_match.group(1)
      footprint_name = footprint_value.split(":")[-1] if ":" in footprint_value else footprint_value

    reference_name = ""
    if reference_match:
      reference_name = reference_match.group(1).strip()

    if symbol_name not in components:
      components[symbol_name] = {
        "symbol": symbol_name,
        "footprint": footprint_name,
      }
    else:
      existing_fp = components[symbol_name]["footprint"]
      if not existing_fp and footprint_name:
        components[symbol_name]["footprint"] = footprint_name

    if footprint_name and footprint_name not in footprint_to_component:
      footprint_to_component[footprint_name] = symbol_name

    if reference_name:
      reference_to_component[reference_name] = symbol_name

    if uuid_match:
      uuid_to_component[uuid_match.group(1)] = symbol_name

  # KiCad projects can store reference/footprint in (symbol_instances) instead
  # of directly in each (symbol ...) instance block.
  symbol_instances_block = find_block_by_token(content, "(symbol_instances")
  if symbol_instances_block is not None and uuid_to_component:
    path_blocks = extract_blocks_by_token(symbol_instances_block, "(path ")

    for path_block in path_blocks:
      path_match = re.search(r'\(path\s+"([^"]+)"\)', path_block)
      if not path_match:
        continue

      path_str = path_match.group(1)
      uuid = path_str.split("/")[-1].strip()
      component_name = uuid_to_component.get(uuid)
      if not component_name:
        continue

      reference_match = re.search(r'\(reference\s+"([^"]+)"\)', path_block)
      if reference_match:
        reference_to_component[reference_match.group(1).strip()] = component_name

      footprint_match = re.search(r'\(footprint\s+"([^"]*)"\)', path_block)
      if footprint_match:
        footprint_value = footprint_match.group(1).strip()
        footprint_name = footprint_value.split(":")[-1] if ":" in footprint_value else footprint_value

        if footprint_name:
          if not components[component_name]["footprint"]:
            components[component_name]["footprint"] = footprint_name

          if footprint_name not in footprint_to_component:
            footprint_to_component[footprint_name] = component_name

  # Fallback if symbol instance parsing failed on unusual schematic formatting.
  if not components:
    for lib_id in re.findall(r'\(lib_id\s+"([^"]+)"\)', content):
      symbol_name = lib_id.split(":")[-1]
      if symbol_name not in components:
        components[symbol_name] = {
          "symbol": symbol_name,
          "footprint": "",
        }

  return content, components, footprint_to_component, reference_to_component


def extract_symbol_definition_map(sch_content):
  lib_symbols_block = find_block_by_token(sch_content, "(lib_symbols")
  if lib_symbols_block is None:
    return {}

  symbol_map = {}
  for block in split_top_level_symbols(lib_symbols_block):
    match = re.match(r'\(symbol\s+"([^"]+)"', block)
    if not match:
      continue

    full_name = match.group(1)
    symbol_name = full_name.split(":")[-1]
    symbol_map[symbol_name] = normalize_symbol_block_name(block, symbol_name)

  return symbol_map


def extract_footprint_map(pcb_path):
  content = pcb_path.read_text(encoding="utf-8")
  footprint_blocks = extract_blocks_by_token(content, "(footprint ")

  footprint_map = {}
  for block in footprint_blocks:
    match = re.match(r'\(footprint\s+"([^"]+)"', block)
    if not match:
      continue

    full_name = match.group(1)
    footprint_name = full_name.split(":")[-1]

    if footprint_name not in footprint_map:
      footprint_map[footprint_name] = normalize_footprint_block_name(block, footprint_name)

  return footprint_map


def collect_footprint_model_paths(board):
  model_paths = {}

  for fp in board.GetFootprints():
    footprint_name = str(fp.GetFPID().GetLibItemName())
    step_path = None

    for model in fp.Models():
      src = Path(resolve_env_path(str(model.m_Filename)))
      ext = src.suffix.lower()
      if ext in [".step", ".stp"] and src.exists():
        step_path = src
        break

    if step_path and footprint_name not in model_paths:
      model_paths[footprint_name] = step_path

  return model_paths


def map_component_footprints_from_board(board, reference_to_component, components):
  component_to_footprint = {}
  board_footprint_names = set()

  for fp in board.GetFootprints():
    ref = str(fp.GetReference()).strip()
    footprint_name = str(fp.GetFPID().GetLibItemName())
    board_footprint_names.add(footprint_name)
    component_name = reference_to_component.get(ref)

    if not component_name:
      continue

    if component_name not in component_to_footprint:
      component_to_footprint[component_name] = footprint_name

  # Fallback: for already localized boards footprint name often equals component name.
  for component_name in components.keys():
    if component_name in component_to_footprint:
      continue
    if component_name in board_footprint_names:
      component_to_footprint[component_name] = component_name

  return component_to_footprint


def export_component_files(
  root,
  components,
  symbol_map,
  footprint_map,
  model_paths,
  component_to_footprint_fallback,
  logger,
):
  components_dir = root / "components"
  components_dir.mkdir(parents=True, exist_ok=True)

  exported = {}
  stats = {
    "components_requested": len(components),
    "missing_symbol_definition": 0,
    "missing_footprint_name": 0,
    "missing_footprint_definition": 0,
    "footprint_from_board_fallback": 0,
    "exported_symbol": 0,
    "exported_footprint": 0,
    "exported_step": 0,
  }

  for component_name in sorted(components.keys()):
    info = components[component_name]
    footprint_name = info["footprint"]
    if not footprint_name:
      fallback_name = component_to_footprint_fallback.get(component_name, "")
      if fallback_name:
        footprint_name = fallback_name
        stats["footprint_from_board_fallback"] += 1

    symbol_block = symbol_map.get(component_name)
    if symbol_block is None:
      logger.warn(f"symbol definition not found for component '{component_name}'")
      stats["missing_symbol_definition"] += 1
      continue

    component_dir = components_dir / component_name
    component_dir.mkdir(parents=True, exist_ok=True)

    has_footprint = False
    footprint_path = component_dir / f"{component_name}.kicad_mod"

    if not footprint_name:
      logger.warn(f"footprint property missing for component '{component_name}'")
      stats["missing_footprint_name"] += 1
    else:
      footprint_block = footprint_map.get(footprint_name)
      if footprint_block is None:
        # If the board was already localized, footprint names may already match component names.
        footprint_block = footprint_map.get(component_name)
        if footprint_block is not None:
          footprint_name = component_name
      if footprint_block is None:
        logger.warn(
          f"footprint definition not found for component '{component_name}'"
          f" (footprint '{footprint_name}')"
        )
        stats["missing_footprint_definition"] += 1
      else:
        mod_text = normalize_footprint_block_name(footprint_block, component_name) + "\n"
        footprint_path.write_text(mod_text, encoding="utf-8")
        has_footprint = True
        stats["exported_footprint"] += 1

    symbol_footprint_ref = f"{LOCAL_LIB}:{component_name}" if has_footprint else ""
    symbol_block_out = rewrite_symbol_footprint_property(symbol_block, symbol_footprint_ref)
    symbol_text = (
      "(kicad_symbol_lib (version 20211014) (generator localizer)\n"
      f"  {symbol_block_out}\n"
      ")\n"
    )
    (component_dir / f"{component_name}.kicad_sym").write_text(symbol_text, encoding="utf-8")
    stats["exported_symbol"] += 1

    step_src = model_paths.get(footprint_name)
    if step_src is None:
      step_src = model_paths.get(component_name)
    has_step = False
    if step_src is not None:
      step_dst = component_dir / f"{component_name}.step"
      shutil.copy2(step_src, step_dst)
      has_step = True
      stats["exported_step"] += 1

    exported[component_name] = {
      "symbol": component_dir / f"{component_name}.kicad_sym",
      "footprint": footprint_path,
      "step": component_dir / f"{component_name}.step",
      "has_step": has_step,
      "has_footprint": has_footprint,
    }

  return exported, stats


def build_combined_symbol_library(root, logger=None):
  libs_dir = root / LOCAL_LIBS_DIR
  libs_dir.mkdir(parents=True, exist_ok=True)

  components_dir = root / "components"
  component_symbol_paths = sorted(components_dir.glob("*/*.kicad_sym"))

  blocks = []
  for sym_path in component_symbol_paths:
    content = sym_path.read_text(encoding="utf-8")
    for block in extract_symbol_blocks(content):
      if re.match(r'\(symbol\s+"([^"]+)"', block):
        blocks.append(block)
        break

  if logger is not None:
    logger.info(f"component symbol files found for localLib: {len(component_symbol_paths)}")
    logger.info(f"symbol blocks added to localLib: {len(blocks)}")

  lib_text = "(kicad_symbol_lib (version 20211014) (generator localizer)\n"
  for block in blocks:
    lib_text += f"  {block}\n"
  lib_text += ")\n"

  local_sym = libs_dir / f"{LOCAL_LIB}.kicad_sym"
  local_sym.write_text(lib_text, encoding="utf-8")
  return local_sym


def build_combined_footprint_library(root, exported_components):
  fp_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  fp_dir.mkdir(parents=True, exist_ok=True)

  for component_name in sorted(exported_components.keys()):
    if not exported_components[component_name]["has_footprint"]:
      continue

    src = exported_components[component_name]["footprint"]
    dst = fp_dir / f"{component_name}.kicad_mod"
    shutil.copy2(src, dst)

  return fp_dir


def build_combined_3d_directory(root, exported_components):
  out_dir = root / LOCAL_LIBS_DIR / "3d"
  out_dir.mkdir(parents=True, exist_ok=True)

  has_step = set()
  for component_name in sorted(exported_components.keys()):
    if not exported_components[component_name]["has_step"]:
      continue

    src = exported_components[component_name]["step"]
    dst = out_dir / f"{component_name}.step"
    shutil.copy2(src, dst)
    has_step.add(component_name)

  return out_dir, has_step


def rewrite_schematic(sch_path, footprint_to_component, exported_names):
  content = sch_path.read_text(encoding="utf-8")

  def rewrite_lib_id(match):
    item_name = match.group(2)
    if item_name not in exported_names:
      return match.group(0)  # leave untouched (power symbols, etc.)
    return f'(lib_id "{LOCAL_LIB}:{item_name}")'

  content = re.sub(
    r'\(lib_id\s+"([^"]+):([^"]+)"\)',
    rewrite_lib_id,
    content,
  )

  def rewrite_symbol_definition_name(match):
    item_name = match.group(2)
    if item_name not in exported_names:
      return match.group(0)  # leave untouched (power/device symbols, etc.)
    return f'(symbol "{LOCAL_LIB}:{item_name}"'

  # Keep in-file cached symbol definitions aligned with rewritten lib_id entries.
  content = re.sub(
    r'\(symbol\s+"([^"]+):([^"]+)"',
    rewrite_symbol_definition_name,
    content,
  )

  def rewrite_footprint_property(match):
    base_name = match.group(2)
    component_name = footprint_to_component.get(base_name, base_name)
    if component_name not in exported_names:
      return match.group(0)  # leave untouched
    return f'(property "Footprint" "{LOCAL_LIB}:{component_name}"'

  content = re.sub(
    r'\(property\s+"Footprint"\s+"([^"]+):([^"]+)"',
    rewrite_footprint_property,
    content,
  )

  sch_path.write_text(content, encoding="utf-8")


def rewrite_board(board, root, footprint_to_component):
  step_dir = root / LOCAL_LIBS_DIR / "3d"
  available_steps = {p.stem for p in step_dir.glob("*.step")}
  changed_models = 0

  for fp in board.GetFootprints():
    old_name = str(fp.GetFPID().GetLibItemName())
    component_name = footprint_to_component.get(old_name, old_name)

    fp.SetFPID(pcbnew.LIB_ID(LOCAL_LIB, component_name))

    models = fp.Models()
    try:
      model_count = len(models)
    except Exception:
      model_count = models.size() if hasattr(models, "size") else 0

    for i in range(model_count):
      model = models[i]
      target_name = component_name

      # Fallback: if the mapped component has no STEP, reuse model basename when possible.
      if target_name not in available_steps:
        model_path = str(model.m_Filename).replace('\\', '/')
        basename = model_path.rsplit('/', 1)[-1]
        stem = basename.rsplit('.', 1)[0] if '.' in basename else basename
        if stem in available_steps:
          target_name = stem

      if target_name in available_steps:
        new_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{target_name}.step"
        if str(model.m_Filename) == new_ref:
          continue
        try:
          models[i].m_Filename = new_ref
          changed_models += 1
        except Exception:
          model.m_Filename = new_ref
          try:
            models[i] = model
            changed_models += 1
          except Exception:
            pass

  return changed_models


def rewrite_board_model_paths_in_file(pcb_path, root):
  content = pcb_path.read_text(encoding="utf-8")
  changed_lines = 0
  transitions = {}

  def model_lib_name(model_ref):
    if model_ref.startswith("${") and "}/" in model_ref:
      return model_ref[2:model_ref.find("}")]
    if model_ref.startswith("$(") and ")/" in model_ref:
      return model_ref[2:model_ref.find(")")]
    if "/" in model_ref:
      return model_ref.split("/", 1)[0]
    return model_ref

  for step_file in sorted((root / LOCAL_LIBS_DIR / "3d").glob("*.step")):
    component_name = step_file.stem
    model_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{component_name}.step"
    pattern = r'\(model\s+"([^"]*/' + re.escape(component_name) + r'\.(?:step|stp))"'

    def repl(match):
      nonlocal changed_lines
      old_ref = match.group(1)
      old_lib = model_lib_name(old_ref)
      new_lib = f"KIPRJMOD/{LOCAL_LIBS_DIR}/3d"
      transitions.setdefault(component_name, set()).add((old_lib, new_lib))
      changed_lines += 1
      return f'(model "{model_ref}"'

    content = re.sub(pattern, repl, content)

  if changed_lines:
    pcb_path.write_text(content, encoding="utf-8")

  return changed_lines, transitions


def rewrite_pretty_model_paths(root):
  pretty_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  if not pretty_dir.exists():
    return 0, {}

  changed_lines = 0
  transitions = {}

  def model_lib_name(model_ref):
    if model_ref.startswith("${") and "}/" in model_ref:
      return model_ref[2:model_ref.find("}")]
    if model_ref.startswith("$(") and ")/" in model_ref:
      return model_ref[2:model_ref.find(")")]
    if "/" in model_ref:
      return model_ref.split("/", 1)[0]
    return model_ref

  for mod_path in pretty_dir.glob("*.kicad_mod"):
    content = mod_path.read_text(encoding="utf-8")
    component_name = mod_path.stem
    step_path = root / LOCAL_LIBS_DIR / "3d" / f"{component_name}.step"
    if not step_path.exists():
      continue

    model_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{component_name}.step"
    pattern = r'\(model\s+"([^"]+)"'

    def repl(match):
      nonlocal changed_lines
      old_ref = match.group(1)
      old_lib = model_lib_name(old_ref)
      new_lib = f"KIPRJMOD/{LOCAL_LIBS_DIR}/3d"
      transitions.setdefault(component_name, set()).add((old_lib, new_lib))
      changed_lines += 1
      return f'(model "{model_ref}"'

    new_content = re.sub(pattern, repl, content)
    if new_content != content:
      mod_path.write_text(new_content, encoding="utf-8")

  return changed_lines, transitions


def log_library_transitions(logger, title, transitions):
  logger.info(title)
  if not transitions:
    logger.info("  none")
    return

  for component_name in sorted(transitions.keys()):
    for old_lib, new_lib in sorted(transitions[component_name]):
      logger.info(f"  {component_name}: {old_lib} -> {new_lib}")


def log_non_kiprjmod_3d_errors(logger, root, pcb_path):
  target_prefix = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/"

  pcb_refs = re.findall(r'\(model\s+"([^"]+)"', pcb_path.read_text(encoding="utf-8"))
  pcb_bad = sorted({ref for ref in pcb_refs if not ref.startswith(target_prefix)})

  pretty_bad = []
  pretty_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  if pretty_dir.exists():
    for mod_path in pretty_dir.glob("*.kicad_mod"):
      refs = re.findall(r'\(model\s+"([^"]+)"', mod_path.read_text(encoding="utf-8"))
      for ref in refs:
        if not ref.startswith(target_prefix):
          pretty_bad.append((mod_path.name, ref))

  if not pcb_bad and not pretty_bad:
    logger.info("3D model path validation: OK (all refs use KIPRJMOD/localLibs/3d)")
    return

  logger.error(
    "3D model path validation FAILED: "
    f"pcb_non_kiprjmod={len(pcb_bad)}, pretty_non_kiprjmod={len(pretty_bad)}"
  )

  for i, ref in enumerate(pcb_bad[:5], start=1):
    logger.error(f"pcb non-kiprjmod[{i}] = {ref}")

  for i, (name, ref) in enumerate(pretty_bad[:5], start=1):
    logger.error(f"pretty non-kiprjmod[{i}] {name} = {ref}")


def log_model_ref_summary(logger, root, pcb_path, phase):
  target_prefix = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/"
  pcb_content = pcb_path.read_text(encoding="utf-8")
  pcb_refs = re.findall(r'\(model\s+"([^"]+)"', pcb_content)
  pcb_bad_refs = [ref for ref in pcb_refs if not ref.startswith(target_prefix)]
  pcb_local_count = sum(1 for ref in pcb_refs if ref.startswith(target_prefix))

  logger.info(
    f"debug {phase} pcb models: total={len(pcb_refs)}, "
    f"non_kiprjmod={len(pcb_bad_refs)}, localLibs={pcb_local_count}"
  )
  for i, ref in enumerate(pcb_bad_refs[:3], start=1):
    logger.warn(f"debug {phase} pcb non_kiprjmod[{i}] = {ref}")

  pretty_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  pretty_total = 0
  pretty_local = 0
  pretty_old_refs = []
  files_scanned = 0

  if pretty_dir.exists():
    for mod_path in pretty_dir.glob("*.kicad_mod"):
      files_scanned += 1
      refs = re.findall(r'\(model\s+"([^"]+)"', mod_path.read_text(encoding="utf-8"))
      pretty_total += len(refs)
      pretty_local += sum(1 for ref in refs if ref.startswith(target_prefix))
      for ref in refs:
        if not ref.startswith(target_prefix):
          pretty_old_refs.append((mod_path.name, ref))

  logger.info(
    f"debug {phase} pretty models: files={files_scanned}, total={pretty_total}, "
    f"non_kiprjmod={len(pretty_old_refs)}, localLibs={pretty_local}"
  )
  for i, (name, ref) in enumerate(pretty_old_refs[:3], start=1):
    logger.warn(f"debug {phase} pretty non_kiprjmod[{i}] {name} = {ref}")


def ensure_tables(root):
  (root / "fp-lib-table").write_text(
    f"""
(fp_lib_table
  (lib (name \"{LOCAL_LIB}\")
    (type \"KiCad\")
    (uri \"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/{LOCAL_LIB}.pretty\")
  )
)
""".strip()
    + "\n",
    encoding="utf-8",
  )

  (root / "sym-lib-table").write_text(
    f"""
(sym_lib_table
  (lib (name \"{LOCAL_LIB}\")
    (type \"KiCad\")
    (uri \"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/{LOCAL_LIB}.kicad_sym\")
  )
)
""".strip()
    + "\n",
    encoding="utf-8",
  )


class KiCadLocalizerPlugin(pcbnew.ActionPlugin):
  def defaults(self):
    self.name = "KiCad Localizer"
    self.category = "Modify PCB"
    self.description = "Export per-component symbol, footprint, and step files"
    self.show_toolbar_button = True

    plugin_dir = Path(__file__).resolve().parent
    icon_candidates = [
      "kicad_localizer.png",  # user-provided filename
      "icon.png",
    ]
    for icon_name in icon_candidates:
      icon_path = plugin_dir / icon_name
      if icon_path.exists():
        self.icon_file_name = str(icon_path)
        break

  def Run(self):
    logger = RuntimeLogger("KiCad Localizer Log")
    logger.info(f"plugin source: {Path(__file__).resolve()}")

    board = pcbnew.GetBoard()
    root = Path(board.GetFileName()).parent
    project_name = Path(board.GetFileName()).stem

    sch_path = root / f"{project_name}.kicad_sch"
    pcb_path = root / f"{project_name}.kicad_pcb"

    if not sch_path.exists() or not pcb_path.exists():
      logger.error("schematic or board file not found")
      return

    logger.info("LOCALIZING...")
    logger.info(f"project root: {root}")
    logger.info("1) Parse schematic and board")

    sch_content, components, footprint_to_component, reference_to_component = parse_used_components(sch_path)
    symbol_map = extract_symbol_definition_map(sch_content)
    footprint_map = extract_footprint_map(pcb_path)
    model_paths = collect_footprint_model_paths(board)
    component_to_footprint_fallback = map_component_footprints_from_board(board, reference_to_component, components)

    logger.info(f"used components found: {len(components)}")
    logger.info(f"symbol definitions found: {len(symbol_map)}")
    logger.info(f"unique board footprints found: {len(footprint_map)}")
    logger.info(f"footprints with STEP models found: {len(model_paths)}")
    logger.info(f"board reference fallback mappings: {len(component_to_footprint_fallback)}")

    if not components:
      logger.error("no used components detected in schematic; aborting")
      return

    logger.info("2) Export per-component files to components/")

    exported, export_stats = export_component_files(
      root,
      components,
      symbol_map,
      footprint_map,
      model_paths,
      component_to_footprint_fallback,
      logger,
    )

    logger.info(
      "export summary: "
      f"symbols={export_stats['exported_symbol']}, "
      f"footprints={export_stats['exported_footprint']}, "
      f"steps={export_stats['exported_step']}"
    )
    logger.info(
      "missing summary: "
      f"symbol_defs={export_stats['missing_symbol_definition']}, "
      f"footprint_names={export_stats['missing_footprint_name']}, "
      f"footprint_defs={export_stats['missing_footprint_definition']}, "
      f"footprint_board_fallback={export_stats['footprint_from_board_fallback']}"
    )

    if not exported:
      logger.error("components/ is empty because no component symbols could be exported")
      return

    logger.info(f"3) Build {LOCAL_LIBS_DIR}/{LOCAL_LIB}.kicad_sym from components/")
    build_combined_symbol_library(root, logger)

    logger.info(f"4) Build {LOCAL_LIBS_DIR}/{LOCAL_LIB}.pretty from components/")
    build_combined_footprint_library(root, exported)

    logger.info(f"5) Build {LOCAL_LIBS_DIR}/3d from components/")
    build_combined_3d_directory(root, exported)

    logger.info("6) Write project library tables")
    ensure_tables(root)

    logger.info("7) Rewrite schematic library references")
    rewrite_schematic(sch_path, footprint_to_component, set(exported.keys()))

    logger.info("8) Rewrite board footprint and 3D references")
    log_model_ref_summary(logger, root, pcb_path, "before")
    mem_updates = rewrite_board(board, root, footprint_to_component)
    logger.info(f"in-memory 3D model entries rewritten: {mem_updates}")
    board.Save(str(pcb_path))
    pretty_models, pretty_transitions = rewrite_pretty_model_paths(root)
    logger.info(f"localLib.pretty 3D model entries rewritten: {pretty_models}")
    changed_models, pcb_transitions = rewrite_board_model_paths_in_file(pcb_path, root)
    logger.info(f"pcb 3D model entries rewritten in file: {changed_models}")
    log_library_transitions(logger, "3D library transitions (pretty):", pretty_transitions)
    log_library_transitions(logger, "3D library transitions (pcb):", pcb_transitions)
    log_model_ref_summary(logger, root, pcb_path, "after")
    log_non_kiprjmod_3d_errors(logger, root, pcb_path)

    logger.info(
      "SUMMARY "
      f"symbols={export_stats['exported_symbol']} "
      f"footprints={export_stats['exported_footprint']} "
      f"steps={export_stats['exported_step']} "
      f"pretty3d={pretty_models} "
      f"pcb3d={changed_models}"
    )

    pcbnew.Refresh()
    logger.info(f"DONE (components + {LOCAL_LIBS_DIR}/)")


KiCadLocalizerPlugin().register()
