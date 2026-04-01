import os
import re
import shutil
import glob
import json
from pathlib import Path

import pcbnew

try:
  import wx
except Exception:
  wx = None


LOCAL_LIB = "localLib"
LOCAL_LIBS_DIR = "localLibs"
MODEL_EXT_PRIORITY = [".step", ".stp", ".wrl"]
REWRITE_SCHEMATIC_REFS = True
SYNC_SCHEMATIC_LIB_SYMBOLS = False

_PATH_VARS_CACHE = {}


def toSafeLocalItemName(raw_name):
  safe = re.sub(r'[\\/:*?"<>|()\[\]{}\-]+', "_", str(raw_name).strip())
  safe = re.sub(r'\s+', "_", safe)
  safe = re.sub(r'_+', "_", safe).strip("._")
  return safe if safe else "component"


def makeUniqueLocalItemName(raw_name, used_names):
  base = toSafeLocalItemName(raw_name)
  candidate = base
  index = 2
  while candidate in used_names:
    candidate = f"{base}_{index}"
    index += 1
  used_names.add(candidate)
  return candidate


def buildUniqueNameMap(raw_names):
  used = set()
  mapped = {}
  for raw_name in sorted(raw_names):
    mapped[raw_name] = makeUniqueLocalItemName(raw_name, used)
  return mapped


def parseVersionKey(path_obj):
  name = path_obj.parent.name
  parts = re.findall(r"\d+", name)
  if not parts:
    return (0,)
  return tuple(int(p) for p in parts)


def loadKicadCommonPathVars():
  candidates = []

  candidates.extend([Path(p) for p in glob.glob(os.path.expanduser("~/Library/Preferences/kicad/*/kicad_common.json"))])
  candidates.extend([Path(p) for p in glob.glob(os.path.expanduser("~/.config/kicad/*/kicad_common.json"))])

  vars_map = {}
  for cfg in sorted(candidates, key=parseVersionKey, reverse=True):
    try:
      data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
      continue

    env_vars = data.get("environment", {}).get("vars", {})
    if isinstance(env_vars, dict):
      for k, v in env_vars.items():
        if isinstance(k, str) and isinstance(v, str):
          vars_map[k] = v

    if vars_map:
      break

  return vars_map


def discoverKicadInstallPathVars():
  install_roots = []
  install_roots.extend(sorted(glob.glob("/Applications/KiCad-*/KiCad-*.app/Contents/SharedSupport")))
  install_roots.extend(sorted(glob.glob("/Applications/KiCad/KiCad.app/Contents/SharedSupport")))

  discovered = {}
  for root in install_roots:
    root_path = Path(root)
    if not root_path.exists():
      continue

    m = re.search(r"KiCad-(\d+)", str(root_path))
    major = m.group(1) if m else None

    three_d = root_path / "3dmodels"
    if three_d.exists():
      if major:
        discovered.setdefault(f"KICAD{major}_3DMODEL_DIR", str(three_d))
      # Some projects still reference legacy KiCad version vars (e.g. KICAD6_3DMODEL_DIR)
      # even when running newer KiCad versions.
      for legacy_major in ("6", "7", "8", "9"):
        discovered.setdefault(f"KICAD{legacy_major}_3DMODEL_DIR", str(three_d))
      discovered.setdefault("KISYS3DMOD", str(three_d))

  return discovered


def discoverUserLibraryPathVars():
  discovered = {}
  candidates = [
    Path(os.path.expanduser("~/Documents/KiCAD/myLibraries")),
    Path(os.path.expanduser("~/Documents/KiCad/myLibraries")),
    Path(os.path.expanduser("~/Documents/KiCAD/MyLibraries")),
    Path(os.path.expanduser("~/Documents/KiCad/MyLibraries")),
  ]

  for candidate in candidates:
    if candidate.exists():
      discovered.setdefault("MYLIBRARIES", str(candidate))
      break

  return discovered


def loadProjectTextVars(project_root):
  if project_root is None:
    return {}

  project_root = Path(project_root)
  pro_files = sorted(project_root.glob("*.kicad_pro"))
  if not pro_files:
    return {}

  try:
    data = json.loads(pro_files[0].read_text(encoding="utf-8"))
  except Exception:
    return {}

  text_vars = data.get("text_variables", {})
  if not isinstance(text_vars, dict):
    return {}

  return {k: v for k, v in text_vars.items() if isinstance(k, str) and isinstance(v, str)}


def getPathVars(project_root=None):
  key = str(project_root) if project_root else "GLOBAL"
  cached = _PATH_VARS_CACHE.get(key)
  if cached is not None:
    return cached

  path_vars = {}
  path_vars.update(os.environ)
  path_vars.update(loadKicadCommonPathVars())
  path_vars.update(discoverKicadInstallPathVars())
  path_vars.update(discoverUserLibraryPathVars())
  path_vars.update(loadProjectTextVars(project_root))

  _PATH_VARS_CACHE[key] = path_vars
  return path_vars


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


def resolveEnvPath(path_str, project_root=None):
  path_vars = getPathVars(project_root)

  def resolveVar(var_name):
    return path_vars.get(var_name)

  def replBrace(match):
    var = match.group(1)
    resolved_var = resolveVar(var)
    return resolved_var if resolved_var else match.group(0)

  def replParen(match):
    var = match.group(1)
    resolved_var = resolveVar(var)
    return resolved_var if resolved_var else match.group(0)

  resolved = re.sub(r"\$\{([^}]+)\}", replBrace, path_str)
  return re.sub(r"\$\(([^)]+)\)", replParen, resolved)


def splitFootprintReference(footprint_ref):
  if not footprint_ref:
    return "", ""

  if ":" in footprint_ref:
    lib_nick, item_name = footprint_ref.split(":", 1)
    return lib_nick.strip(), item_name.strip()

  return "", footprint_ref.strip()


def findBalancedBlock(content, start_index):
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


def findBlockByToken(content, token):
  start = content.find(token)
  if start == -1:
    return None
  return findBalancedBlock(content, start)


def extractBlocksByToken(content, token):
  blocks = []
  idx = 0

  while True:
    start = content.find(token, idx)
    if start == -1:
      break

    block = findBalancedBlock(content, start)
    if block is None:
      break

    blocks.append(block)
    idx = start + len(block)

  return blocks


def extractSymbolBlocks(content):
  blocks = []

  # Match only real '(symbol ...' openings and avoid '(symbol_instances ...'.
  for match in re.finditer(r'\(symbol(?=[\s\)])', content):
    start = match.start()
    block = findBalancedBlock(content, start)
    if block is None:
      continue
    blocks.append(block)

  return blocks


def extractDirectChildBlocks(root_block, token):
  blocks = []
  depth = 0
  i = 0

  while i < len(root_block):
    ch = root_block[i]

    if ch == "(":
      if depth == 1 and root_block.startswith(token, i):
        block = findBalancedBlock(root_block, i)
        if block is None:
          break
        blocks.append(block)
        i += len(block)
        continue

      depth += 1
      i += 1
      continue

    if ch == ")":
      depth -= 1

    i += 1

  return blocks


def splitTopLevelSymbols(lib_symbols_block):
  body = lib_symbols_block[len("(lib_symbols"):]
  if body.endswith(")"):
    body = body[:-1]

  symbols = []
  i = 0

  while i < len(body):
    start = body.find("(symbol ", i)
    if start == -1:
      break

    block = findBalancedBlock(body, start)
    if block is None:
      break

    symbols.append(block)
    i = start + len(block)

  return symbols


def getSymbolBlockName(symbol_block):
  match = re.match(r'\(symbol\s+"([^"]+)"', symbol_block)
  return match.group(1) if match else None


def prefixSymbolBlockName(symbol_block, library_name):
  symbol_name = getSymbolBlockName(symbol_block)
  if symbol_name is None:
    return symbol_block

  qualified_name = symbol_name if ":" in symbol_name else f"{library_name}:{symbol_name}"
  return re.sub(
    r'\(symbol\s+"([^"]+)"',
    f'(symbol "{qualified_name}"',
    symbol_block,
    count=1,
  )


def normalizeSymbolBlockName(symbol_block, symbol_name):
  # Normalize every symbol declaration in the block to local names without
  # library prefixes (e.g. Device:R_0_1 -> R_0_1).
  top_level_match = re.match(r'\(symbol\s+"([^"]+)"', symbol_block)
  if top_level_match is None:
    return symbol_block

  original_root = top_level_match.group(1).split(":")[-1]

  def repl(match):
    raw_name = match.group(1)
    local_name = raw_name.split(":")[-1]

    if local_name == original_root:
      target_name = symbol_name
    elif local_name.startswith(original_root + "_"):
      target_name = symbol_name + local_name[len(original_root):]
    else:
      target_name = toSafeLocalItemName(local_name)

    return f'(symbol "{target_name}"'

  normalized = re.sub(
    r'\(symbol\s+"([^"]+)"',
    repl,
    symbol_block,
  )

  # Ensure the top-level declaration matches the exported component name.
  return re.sub(
    r'\(symbol\s+"([^"]+)"',
    f'(symbol "{symbol_name}"',
    normalized,
    count=1,
  )


def normalizeFootprintBlockName(footprint_block, footprint_name):
  return re.sub(
    r'\(footprint\s+"([^"]+)"',
    f'(footprint "{footprint_name}"',
    footprint_block,
    count=1,
  )


def rewriteSymbolFootprintProperty(symbol_block, footprint_ref):
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


def parseUsedComponents(sch_path):
  content = sch_path.read_text(encoding="utf-8")
  symbol_blocks = extractSymbolBlocks(content)

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
    footprint_full = footprint_match.group(1).strip() if footprint_match else ""

    reference_name = ""
    if reference_match:
      reference_name = reference_match.group(1).strip()

    if symbol_name not in components:
      components[symbol_name] = {
        "symbol": symbol_name,
        "footprint": footprint_name,
        "footprint_full": footprint_full,
      }
    else:
      existing_fp = components[symbol_name]["footprint"]
      if not existing_fp and footprint_name:
        components[symbol_name]["footprint"] = footprint_name
      existing_full = components[symbol_name].get("footprint_full", "")
      if not existing_full and footprint_full:
        components[symbol_name]["footprint_full"] = footprint_full

    if footprint_name and footprint_name not in footprint_to_component:
      footprint_to_component[footprint_name] = symbol_name

    if reference_name:
      reference_to_component[reference_name] = symbol_name

    if uuid_match:
      uuid_to_component[uuid_match.group(1)] = symbol_name

  # KiCad projects can store reference/footprint in (symbol_instances) instead
  # of directly in each (symbol ...) instance block.
  symbol_instances_block = findBlockByToken(content, "(symbol_instances")
  if symbol_instances_block is not None and uuid_to_component:
    path_blocks = extractBlocksByToken(symbol_instances_block, "(path ")

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

        if footprint_value and not components[component_name].get("footprint_full"):
          components[component_name]["footprint_full"] = footprint_value

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


def extractSymbolDefinitionMap(sch_content):
  lib_symbols_block = findBlockByToken(sch_content, "(lib_symbols")
  if lib_symbols_block is None:
    return {}

  symbol_map = {}
  for block in splitTopLevelSymbols(lib_symbols_block):
    match = re.match(r'\(symbol\s+"([^"]+)"', block)
    if not match:
      continue

    full_name = match.group(1)
    symbol_name = full_name.split(":")[-1]
    symbol_map[symbol_name] = normalizeSymbolBlockName(block, symbol_name)

  return symbol_map


def extractFootprintMap(pcb_path):
  content = pcb_path.read_text(encoding="utf-8")
  footprint_blocks = extractBlocksByToken(content, "(footprint ")

  footprint_map = {}
  for block in footprint_blocks:
    match = re.match(r'\(footprint\s+"([^"]+)"', block)
    if not match:
      continue

    full_name = match.group(1)
    footprint_name = full_name.split(":")[-1]

    if footprint_name not in footprint_map:
      footprint_map[footprint_name] = normalizeFootprintBlockName(block, footprint_name)

  return footprint_map


def pickPreferredModelPath(model_candidates):
  if not model_candidates:
    return None

  by_ext = {Path(p).suffix.lower(): Path(p) for p in model_candidates}
  for ext in MODEL_EXT_PRIORITY:
    if ext in by_ext:
      return by_ext[ext]

  return Path(model_candidates[0])


def preferStepSibling(model_path):
  model_path = Path(model_path)
  if not model_path.exists():
    return None

  if model_path.suffix.lower() in (".step", ".stp"):
    return model_path

  step_candidate = model_path.with_suffix(".step")
  if step_candidate.exists():
    return step_candidate

  stp_candidate = model_path.with_suffix(".stp")
  if stp_candidate.exists():
    return stp_candidate

  return model_path


def collectSiblingModelVariants(model_path):
  model_path = Path(model_path)
  if not model_path.exists():
    return []

  variants = []
  for ext in MODEL_EXT_PRIORITY:
    candidate = model_path.with_suffix(ext)
    if candidate.exists():
      variants.append(candidate)

  # If none of the preferred extensions exists, keep original path.
  if not variants:
    variants.append(model_path)

  unique = []
  seen = set()
  for p in variants:
    key = str(p)
    if key in seen:
      continue
    seen.add(key)
    unique.append(p)

  return unique


def collectFootprintModelPaths(board):
  model_paths = {}

  for fp in board.GetFootprints():
    footprint_name = str(fp.GetFPID().GetLibItemName())
    model_candidates = []

    for model in fp.Models():
      src = Path(resolveEnvPath(str(model.m_Filename), Path(board.GetFileName()).parent))
      ext = src.suffix.lower()
      if ext in MODEL_EXT_PRIORITY and src.exists():
        preferred = preferStepSibling(src)
        if preferred is not None:
          model_candidates.append(preferred)

    best_model = pickPreferredModelPath(model_candidates)
    if best_model is not None and footprint_name not in model_paths:
      model_paths[footprint_name] = best_model

  return model_paths


def resolveModelFromFootprintLibrary(footprint_ref, project_root):
  lib_nick, item_name = splitFootprintReference(footprint_ref)
  if not lib_nick or not item_name:
    return None

  try:
    loaded_fp = pcbnew.FootprintLoad(lib_nick, item_name)
  except Exception:
    return None

  if loaded_fp is None:
    return None

  model_candidates = []
  for model in loaded_fp.Models():
    src = Path(resolveEnvPath(str(model.m_Filename), project_root))
    if src.suffix.lower() in MODEL_EXT_PRIORITY and src.exists():
      preferred = preferStepSibling(src)
      if preferred is not None:
        model_candidates.append(preferred)

  return pickPreferredModelPath(model_candidates)


def resolveModelFromFootprintBlock(footprint_block, project_root):
  model_candidates = []
  for model_ref in re.findall(r'\(model\s+"([^"]+)"', footprint_block):
    src = Path(resolveEnvPath(model_ref, project_root))
    ext = src.suffix.lower()
    if ext in MODEL_EXT_PRIORITY and src.exists():
      preferred = preferStepSibling(src)
      if preferred is not None:
        model_candidates.append(preferred)

  return pickPreferredModelPath(model_candidates)


def countLocalizedModelRefs(board):
  localized_prefix = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/"
  localized_refs = 0

  for fp in board.GetFootprints():
    for model in fp.Models():
      if str(model.m_Filename).startswith(localized_prefix):
        localized_refs += 1

  return localized_refs


def mapComponentModelsFromBoard(board, reference_to_component, project_root=None):
  component_model_candidates = {}

  for fp in board.GetFootprints():
    ref = str(fp.GetReference()).strip()
    component_name = reference_to_component.get(ref)
    if not component_name:
      continue

    for model in fp.Models():
      src = Path(resolveEnvPath(str(model.m_Filename), project_root))
      ext = src.suffix.lower()
      if ext in MODEL_EXT_PRIORITY and src.exists():
        preferred = preferStepSibling(src)
        if preferred is not None:
          component_model_candidates.setdefault(component_name, []).append(preferred)

  component_model_map = {}
  for component_name, candidates in component_model_candidates.items():
    best = pickPreferredModelPath(candidates)
    if best is not None:
      component_model_map[component_name] = best

  return component_model_map


def mapComponentFootprintsFromBoard(board, reference_to_component, components):
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


def exportComponentFiles(
  root,
  components,
  symbol_map,
  footprint_map,
  footprint_name_map,
  model_paths,
  component_model_map,
  component_to_footprint_fallback,
  logger,
):
  components_dir = root / "components"
  components_dir.mkdir(parents=True, exist_ok=True)

  exported = {}
  component_name_map = {}
  used_local_names = set()
  stats = {
    "components_requested": len(components),
    "missing_symbol_definition": 0,
    "missing_footprint_name": 0,
    "missing_footprint_definition": 0,
    "footprint_from_board_fallback": 0,
    "exported_symbol": 0,
    "exported_footprint": 0,
    "exported_model": 0,
    "step_from_fp_library": 0,
    "model_from_component_board_map": 0,
  }

  for component_name in sorted(components.keys()):
    local_name = makeUniqueLocalItemName(component_name, used_local_names)
    component_name_map[component_name] = local_name
    if local_name != component_name:
      logger.warn(f"component '{component_name}' will be localized as '{local_name}'")

    info = components[component_name]
    footprint_name = info["footprint"]
    footprint_full = info.get("footprint_full", "")
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

    component_dir = components_dir / local_name
    component_dir.mkdir(parents=True, exist_ok=True)

    has_footprint = False
    local_footprint_name = local_name
    footprint_path = component_dir / f"{local_footprint_name}.kicad_mod"

    if not footprint_name:
      logger.info(
        f"skipping schematic-only component '{component_name}' "
        "because it has no footprint"
      )
      stats["missing_footprint_name"] += 1
      continue
    else:
      footprint_block = footprint_map.get(footprint_name)
      if footprint_block is None:
        # If the board was already localized, footprint names may already match component names.
        footprint_block = footprint_map.get(component_name)
        if footprint_block is not None:
          footprint_name = component_name
      if footprint_block is None:
        logger.warn(
          f"skipping component '{component_name}' because footprint definition "
          f"'{footprint_name}' was not found on the board"
        )
        stats["missing_footprint_definition"] += 1
        continue
      else:
        local_footprint_name = footprint_name_map.get(footprint_name, local_name)
        footprint_path = component_dir / f"{local_footprint_name}.kicad_mod"
        mod_text = normalizeFootprintBlockName(footprint_block, local_footprint_name) + "\n"
        footprint_path.write_text(mod_text, encoding="utf-8")
        has_footprint = True
        stats["exported_footprint"] += 1

    symbol_block_local = normalizeSymbolBlockName(symbol_block, local_name)
    symbol_footprint_ref = f"{LOCAL_LIB}:{local_footprint_name}"
    symbol_block_out = rewriteSymbolFootprintProperty(symbol_block_local, symbol_footprint_ref)
    symbol_text = (
      "(kicad_symbol_lib (version 20211014) (generator localizer)\n"
      f"  {symbol_block_out}\n"
      ")\n"
    )
    (component_dir / f"{local_name}.kicad_sym").write_text(symbol_text, encoding="utf-8")
    stats["exported_symbol"] += 1

    model_src = component_model_map.get(component_name)
    if model_src is not None:
      stats["model_from_component_board_map"] += 1
    if model_src is None:
      model_src = model_paths.get(footprint_name)
    if model_src is None:
      model_src = model_paths.get(component_name)
    if model_src is None and footprint_full:
      model_src = resolveModelFromFootprintLibrary(footprint_full, root)
      if model_src is not None:
        stats["step_from_fp_library"] += 1
    if model_src is None and footprint_block is not None:
      model_src = resolveModelFromFootprintBlock(footprint_block, root)
      if model_src is not None:
        stats["step_from_fp_library"] += 1

    has_model = False
    model_ext = ""
    model_path = None
    model_variants = []
    if model_src is not None:
      source_variants = collectSiblingModelVariants(model_src)
      copied_variants = []
      for src_variant in source_variants:
        ext = src_variant.suffix.lower()
        dst_variant = component_dir / f"{local_name}{ext}"
        shutil.copy2(src_variant, dst_variant)
        copied_variants.append(dst_variant)

      preferred_src = pickPreferredModelPath(source_variants)
      model_ext = preferred_src.suffix.lower() if preferred_src is not None else ""
      model_path = component_dir / f"{local_name}{model_ext}" if model_ext else None
      model_variants = copied_variants
      has_model = True
      stats["exported_model"] += 1

    exported[component_name] = {
      "symbol": component_dir / f"{local_name}.kicad_sym",
      "footprint": footprint_path,
      "model": model_path,
      "model_variants": model_variants,
      "model_ext": model_ext,
      "has_model": has_model,
      "has_footprint": has_footprint,
      "local_name": local_name,
      "local_footprint_name": local_footprint_name,
    }

  return exported, stats, component_name_map


def collectAllBoardModelSourceFiles(board, root):
  source_files = {}

  for fp in board.GetFootprints():
    for model in fp.Models():
      resolved = Path(resolveEnvPath(str(model.m_Filename), root))
      ext = resolved.suffix.lower()
      if ext not in MODEL_EXT_PRIORITY or not resolved.exists():
        continue

      for variant in collectSiblingModelVariants(resolved):
        key = f"{variant.stem}{variant.suffix.lower()}"
        source_files.setdefault(key, variant)

  return list(source_files.values())


def buildCombinedSymbolLibrary(root, logger=None):
  libs_dir = root / LOCAL_LIBS_DIR
  libs_dir.mkdir(parents=True, exist_ok=True)

  components_dir = root / "components"
  component_symbol_paths = sorted(components_dir.glob("*/*.kicad_sym"))

  blocks = []
  for sym_path in component_symbol_paths:
    content = sym_path.read_text(encoding="utf-8")
    for block in extractSymbolBlocks(content):
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


def buildCombinedFootprintLibrary(root, exported_components):
  fp_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  fp_dir.mkdir(parents=True, exist_ok=True)

  for component_name in sorted(exported_components.keys()):
    if not exported_components[component_name]["has_footprint"]:
      continue

    src = exported_components[component_name]["footprint"]
    local_name = exported_components[component_name].get("local_name", component_name)
    dst = fp_dir / f"{local_name}.kicad_mod"
    shutil.copy2(src, dst)

  return fp_dir


def exportAllBoardFootprintsToLocalLibrary(root, footprint_map, footprint_name_map):
  fp_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  fp_dir.mkdir(parents=True, exist_ok=True)

  written = 0
  for original_name, footprint_block in sorted(footprint_map.items()):
    local_name = footprint_name_map.get(original_name)
    if not local_name:
      continue

    dst = fp_dir / f"{local_name}.kicad_mod"
    mod_text = normalizeFootprintBlockName(footprint_block, local_name) + "\n"
    dst.write_text(mod_text, encoding="utf-8")
    written += 1

  return written


def buildCombined3dDirectory(root, exported_components, board=None):
  out_dir = root / LOCAL_LIBS_DIR / "3d"
  out_dir.mkdir(parents=True, exist_ok=True)

  model_ext_by_component = {}
  for component_name in sorted(exported_components.keys()):
    if not exported_components[component_name]["has_model"]:
      continue

    local_name = exported_components[component_name].get("local_name", component_name)

    variants = exported_components[component_name].get("model_variants") or []
    for src_variant in variants:
      ext = src_variant.suffix.lower()
      dst_variant = out_dir / f"{local_name}{ext}"
      shutil.copy2(src_variant, dst_variant)

    ext = exported_components[component_name]["model_ext"]
    if ext:
      model_ext_by_component[local_name] = ext

  if board is not None:
    for src in collectAllBoardModelSourceFiles(board, root):
      dst = out_dir / src.name
      if not dst.exists():
        shutil.copy2(src, dst)

  return out_dir, model_ext_by_component


def rewriteSchematic(sch_path, component_name_map, footprint_name_map):
  content = sch_path.read_text(encoding="utf-8")
  rewritten_lib_ids = 0
  rewritten_footprints = 0
  rewritten_lib_symbols = 0
  rewritten_cache_footprints = 0
  rewritten_cache_sanitized = 0

  lib_symbols_block = findBlockByToken(content, "(lib_symbols")
  placeholder = "__LIB_SYMBOLS_BLOCK__"
  if lib_symbols_block is not None:
    working_content = content.replace(lib_symbols_block, placeholder, 1)
  else:
    working_content = content

  def rewriteLibId(match):
    nonlocal rewritten_lib_ids
    lib_name = match.group(1)
    if lib_name == "power":
      return match.group(0)
    item_name = match.group(2)
    local_name = component_name_map.get(item_name)
    if local_name is None:
      return match.group(0)  # leave untouched (power symbols, etc.)
    rewritten_lib_ids += 1
    return f'(lib_id "{LOCAL_LIB}:{local_name}")'

  working_content = re.sub(
    r'\(lib_id\s+"([^"]+):([^"]+)"\)',
    rewriteLibId,
    working_content,
  )

  def rewriteFootprintProperty(match):
    nonlocal rewritten_footprints
    footprint_ref = match.group(2)
    _, base_name = splitFootprintReference(footprint_ref)
    base_name = base_name or footprint_ref
    local_name = footprint_name_map.get(base_name)
    if local_name is None:
      return match.group(0)  # leave untouched
    rewritten_footprints += 1
    return f'{match.group(1)}{LOCAL_LIB}:{local_name}{match.group(3)}'

  working_content = re.sub(
    r'(\(property\s+"Footprint"\s+")([^"]*)(")',
    rewriteFootprintProperty,
    working_content,
  )

  if lib_symbols_block is not None:
    cache_block = lib_symbols_block

    # Keep lib_symbols cache aligned with localized instance refs.
    def rewriteCacheSymbolName(match):
      nonlocal rewritten_lib_symbols
      lib_name = match.group(1)
      item_name = match.group(2)
      if lib_name == "power":
        return match.group(0)

      local_name = component_name_map.get(item_name)
      if local_name is None:
        return match.group(0)

      rewritten_lib_symbols += 1
      return f'(symbol "{LOCAL_LIB}:{local_name}"'

    cache_block = re.sub(
      r'\(symbol\s+"([^":]+):([^"]+)"',
      rewriteCacheSymbolName,
      cache_block,
    )

    # Localize cached Footprint properties in lib_symbols as well.
    def rewriteCacheFootprintProperty(match):
      nonlocal rewritten_cache_footprints
      footprint_ref = match.group(2)
      _, base_name = splitFootprintReference(footprint_ref)
      base_name = base_name or footprint_ref
      local_name = footprint_name_map.get(base_name)
      if local_name is None:
        return match.group(0)
      rewritten_cache_footprints += 1
      return f'{match.group(1)}{LOCAL_LIB}:{local_name}{match.group(3)}'

    cache_block = re.sub(
      r'(\(property\s+"Footprint"\s+")([^"]*)(")',
      rewriteCacheFootprintProperty,
      cache_block,
    )

    # Targeted sanitize for known unsafe cached ESP32 symbol names.
    sanitize_replacements = [
      (
        '(symbol "MyNewLibrary:ESP32-WROVER-B/E_(PSRAM)"',
        '(symbol "localLib:ESP32_WROVER_B_E_PSRAM"',
      ),
      (
        '(property "Footprint" "MyNewLibrary:ESP32-WROVER-B-E_(PSRAM)"',
        '(property "Footprint" "localLib:ESP32_WROVER_B_E_PSRAM"',
      ),
      (
        '(symbol "ESP32-WROVER-B/E_(PSRAM)_0_0"',
        '(symbol "ESP32_WROVER_B_E_PSRAM_0_0"',
      ),
    ]

    for old_text, new_text in sanitize_replacements:
      if old_text in cache_block:
        rewritten_cache_sanitized += cache_block.count(old_text)
        cache_block = cache_block.replace(old_text, new_text)

    lib_symbols_block = cache_block

  if lib_symbols_block is not None:
    content = working_content.replace(placeholder, lib_symbols_block, 1)
  else:
    content = working_content

  sch_path.write_text(content, encoding="utf-8")
  return {
    "lib_ids": rewritten_lib_ids,
    "footprints": rewritten_footprints,
    "lib_symbols": rewritten_lib_symbols,
    "cache_footprints": rewritten_cache_footprints,
    "cache_sanitized": rewritten_cache_sanitized,
  }


def removeLibTableEntriesByName(table_path, names_to_remove):
  if not table_path.exists():
    return 0

  content = table_path.read_text(encoding="utf-8")
  updated = content
  removed = 0
  scan_index = 0

  while True:
    start = updated.find("(lib ", scan_index)
    if start == -1:
      break

    block = findBalancedBlock(updated, start)
    if block is None:
      break

    matched_name = None
    for lib_name in names_to_remove:
      if re.search(r'\(name\s+"' + re.escape(lib_name) + r'"\)', block):
        matched_name = lib_name
        break

    if matched_name is None:
      scan_index = start + len(block)
      continue

    updated = updated[:start] + updated[start + len(block):]
    removed += 1
    scan_index = 0

  if updated != content:
    updated = re.sub(r'\n{3,}', '\n\n', updated)
    table_path.write_text(updated, encoding="utf-8")

  return removed


def syncSchematicLibSymbolsFromLocalLibrary(root, sch_path, exported_names, logger=None):
  local_sym_path = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.kicad_sym"
  if not local_sym_path.exists():
    if logger is not None:
      logger.warn(f"local symbol library not found: {local_sym_path}")
    return 0

  sch_content = sch_path.read_text(encoding="utf-8")
  sch_lib_symbols_block = findBlockByToken(sch_content, "(lib_symbols")
  if sch_lib_symbols_block is None:
    if logger is not None:
      logger.warn("schematic has no lib_symbols block")
    return 0

  local_content = local_sym_path.read_text(encoding="utf-8")
  local_root = findBlockByToken(local_content, "(kicad_symbol_lib")
  if local_root is None:
    if logger is not None:
      logger.warn("localLib.kicad_sym has no kicad_symbol_lib root")
    return 0

  local_symbol_blocks = extractDirectChildBlocks(local_root, "(symbol ")
  if not local_symbol_blocks:
    if logger is not None:
      logger.warn("no top-level symbols found in localLib.kicad_sym")
    return 0

  existing_blocks = splitTopLevelSymbols(sch_lib_symbols_block)
  preserved_blocks = []
  for block in existing_blocks:
    block_name = getSymbolBlockName(block)
    if block_name is None:
      continue

    local_name = block_name.split(":")[-1]
    if local_name in exported_names:
      continue

    preserved_blocks.append(block)

  updated_blocks = list(preserved_blocks)
  for block in local_symbol_blocks:
    updated_blocks.append(prefixSymbolBlockName(block, LOCAL_LIB))

  new_block_lines = ["(lib_symbols"]
  for block in updated_blocks:
    for line in block.splitlines():
      new_block_lines.append(f"\t\t{line}")
  new_block_lines.append("\t)")
  new_lib_symbols_block = "\n".join(new_block_lines)

  updated_content = sch_content.replace(sch_lib_symbols_block, new_lib_symbols_block, 1)
  sch_path.write_text(updated_content, encoding="utf-8")
  return len(local_symbol_blocks)


def upsertProjectLibTable(table_path, table_type, name, uri):
  if table_type == "sym":
    root_token = "sym_lib_table"
  else:
    root_token = "fp_lib_table"

  lib_entry = (
    f'  (lib (name "{name}")\n'
    f'    (type "KiCad")\n'
    f'    (uri "{uri}")\n'
    "  )\n"
  )

  if table_path.exists():
    content = table_path.read_text(encoding="utf-8")
  else:
    content = f"({root_token}\n)\n"

  if re.search(r'\(lib\s+\(name\s+"' + re.escape(name) + r'"\)', content):
    # Replace existing entry for this library name.
    pattern = (
      r'\(lib\s+\(name\s+"' + re.escape(name) + r'"\)'  # start of target lib
      r'(?:\s|\n)*\(type\s+"[^"]+"\)'
      r'(?:\s|\n)*\(uri\s+"[^"]+"\)'
      r'(?:\s|\n)*\)'
    )
    content = re.sub(pattern, lib_entry.rstrip(), content, count=1)
  else:
    # Insert before final ')' of table root.
    idx = content.rfind(")")
    if idx == -1:
      content = f"({root_token}\n{lib_entry})\n"
    else:
      content = content[:idx] + lib_entry + content[idx:]

  table_path.write_text(content, encoding="utf-8")


def rewriteBoard(board, root, footprint_name_map, reference_to_component, component_name_map):
  out_dir = root / LOCAL_LIBS_DIR / "3d"
  out_dir.mkdir(parents=True, exist_ok=True)

  model_dir = root / LOCAL_LIBS_DIR / "3d"
  model_ext_by_component = {
    p.stem: p.suffix.lower()
    for p in model_dir.glob("*")
    if p.is_file() and p.suffix.lower() in MODEL_EXT_PRIORITY
  }
  changed_models = 0

  for fp in board.GetFootprints():
    old_name = str(fp.GetFPID().GetLibItemName())
    local_footprint_name = footprint_name_map.get(old_name)
    if local_footprint_name is not None:
      fp.SetFPID(pcbnew.LIB_ID(LOCAL_LIB, local_footprint_name))

    ref = str(fp.GetReference()).strip()
    component_name = reference_to_component.get(ref)
    if not component_name:
      component_name = old_name

    local_name = component_name_map.get(component_name)

    models = fp.Models()
    try:
      model_count = len(models)
    except Exception:
      model_count = models.size() if hasattr(models, "size") else 0

    for i in range(model_count):
      model = models[i]
      model_ref = str(model.m_Filename)
      basename = model_ref.replace('\\', '/').rsplit('/', 1)[-1]
      stem = basename.rsplit('.', 1)[0] if '.' in basename else basename
      ext = Path(basename).suffix.lower()

      target_name = local_name if local_name else stem
      target_ext = model_ext_by_component.get(target_name)

      # Fallback: if the mapped component has no model, reuse model basename when possible.
      if target_ext is None:
        if stem in model_ext_by_component:
          target_name = stem
          target_ext = model_ext_by_component.get(stem)

      # Final fallback: always localize reference using component local name.
      if target_ext is None:
        resolved = Path(resolveEnvPath(model_ref, root))
        if resolved.exists() and ext in MODEL_EXT_PRIORITY:
          dst = out_dir / f"{target_name}{ext}"
          if not dst.exists():
            try:
              shutil.copy2(resolved, dst)
            except Exception:
              pass
          target_ext = ext
        else:
          target_ext = ext if ext in MODEL_EXT_PRIORITY else ".step"

      if target_ext is not None:
        new_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{target_name}{target_ext}"
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


def rewriteBoardModelPathsInFile(pcb_path, root):
  content = pcb_path.read_text(encoding="utf-8")
  changed_lines = 0
  transitions = {}

  def modelLibName(model_ref):
    if model_ref.startswith("${") and "}/" in model_ref:
      return model_ref[2:model_ref.find("}")]
    if model_ref.startswith("$(") and ")/" in model_ref:
      return model_ref[2:model_ref.find(")")]
    if "/" in model_ref:
      return model_ref.split("/", 1)[0]
    return model_ref

  model_files = sorted(
    [p for p in (root / LOCAL_LIBS_DIR / "3d").glob("*") if p.is_file() and p.suffix.lower() in MODEL_EXT_PRIORITY]
  )

  for model_file in model_files:
    component_name = model_file.stem
    model_ext = model_file.suffix.lower()
    model_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{component_name}{model_ext}"
    pattern = r'\(model\s+"([^"]*/' + re.escape(component_name) + r'\.(?:step|stp|wrl))"'

    def repl(match):
      nonlocal changed_lines
      old_ref = match.group(1)
      old_lib = modelLibName(old_ref)
      new_lib = f"KIPRJMOD/{LOCAL_LIBS_DIR}/3d"
      transitions.setdefault(component_name, set()).add((old_lib, new_lib))
      changed_lines += 1
      return f'(model "{model_ref}"'

    content = re.sub(pattern, repl, content)

  if changed_lines:
    pcb_path.write_text(content, encoding="utf-8")

  return changed_lines, transitions


def rewritePrettyModelPaths(root):
  pretty_dir = root / LOCAL_LIBS_DIR / f"{LOCAL_LIB}.pretty"
  if not pretty_dir.exists():
    return 0, {}

  changed_lines = 0
  transitions = {}
  local_3d_dir = root / LOCAL_LIBS_DIR / "3d"

  def modelLibName(model_ref):
    if model_ref.startswith("${") and "}/" in model_ref:
      return model_ref[2:model_ref.find("}")]
    if model_ref.startswith("$(") and ")/" in model_ref:
      return model_ref[2:model_ref.find(")")]
    if "/" in model_ref:
      return model_ref.split("/", 1)[0]
    return model_ref

  for mod_path in pretty_dir.glob("*.kicad_mod"):
    content = mod_path.read_text(encoding="utf-8")
    pattern = r'\(model\s+"([^"]+)"'

    def repl(match):
      nonlocal changed_lines
      old_ref = match.group(1)
      basename = old_ref.replace('\\', '/').rsplit('/', 1)[-1]
      stem = basename.rsplit('.', 1)[0] if '.' in basename else basename

      model_path = None
      for ext in MODEL_EXT_PRIORITY:
        candidate = local_3d_dir / f"{stem}{ext}"
        if candidate.exists():
          model_path = candidate
          break

      if model_path is None:
        desired_stem = mod_path.stem
        for ext in MODEL_EXT_PRIORITY:
          candidate = local_3d_dir / f"{desired_stem}{ext}"
          if candidate.exists():
            model_path = candidate
            stem = desired_stem
            break

      # Final fallback: force localLibs path with footprint stem and original ext.
      if model_path is None:
        desired_stem = mod_path.stem
        old_ext = Path(old_ref.replace('\\', '/')).suffix.lower()
        if old_ext not in MODEL_EXT_PRIORITY:
          old_ext = ".step"

        resolved_old = Path(resolveEnvPath(old_ref, root))
        if resolved_old.exists() and resolved_old.suffix.lower() in MODEL_EXT_PRIORITY:
          target_copy = local_3d_dir / f"{desired_stem}{resolved_old.suffix.lower()}"
          if not target_copy.exists():
            try:
              shutil.copy2(resolved_old, target_copy)
            except Exception:
              pass
          old_ext = resolved_old.suffix.lower()

        model_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{desired_stem}{old_ext}"
        old_lib = modelLibName(old_ref)
        new_lib = f"KIPRJMOD/{LOCAL_LIBS_DIR}/3d"
        transitions.setdefault(desired_stem, set()).add((old_lib, new_lib))
        changed_lines += 1
        return f'(model "{model_ref}"'

      model_ref = f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/3d/{stem}{model_path.suffix.lower()}"
      old_lib = modelLibName(old_ref)
      new_lib = f"KIPRJMOD/{LOCAL_LIBS_DIR}/3d"
      transitions.setdefault(stem, set()).add((old_lib, new_lib))
      changed_lines += 1
      return f'(model "{model_ref}"'

    new_content = re.sub(pattern, repl, content)
    if new_content != content:
      mod_path.write_text(new_content, encoding="utf-8")

  return changed_lines, transitions


def logLibraryTransitions(logger, title, transitions):
  logger.info(title)
  if not transitions:
    logger.info("  none")
    return

  for component_name in sorted(transitions.keys()):
    for old_lib, new_lib in sorted(transitions[component_name]):
      logger.info(f"  {component_name}: {old_lib} -> {new_lib}")


def logNonKiprjmod3dErrors(logger, root, pcb_path):
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


def logModelRefSummary(logger, root, pcb_path, phase):
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


def ensureTables(root):
  removeLibTableEntriesByName(
    root / "sym-lib-table",
    {
      "esp32-wrover-b:e_(psram)",
    },
  )

  upsertProjectLibTable(
    root / "fp-lib-table",
    "fp",
    LOCAL_LIB,
    f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/{LOCAL_LIB}.pretty",
  )

  upsertProjectLibTable(
    root / "sym-lib-table",
    "sym",
    LOCAL_LIB,
    f"${{KIPRJMOD}}/{LOCAL_LIBS_DIR}/{LOCAL_LIB}.kicad_sym",
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

    sch_content, components, footprint_to_component, reference_to_component = parseUsedComponents(sch_path)
    symbol_map = extractSymbolDefinitionMap(sch_content)
    footprint_map = extractFootprintMap(pcb_path)
    footprint_name_map = buildUniqueNameMap(footprint_map.keys())
    model_paths = collectFootprintModelPaths(board)
    component_model_map = mapComponentModelsFromBoard(board, reference_to_component, root)
    localized_model_refs = countLocalizedModelRefs(board)
    component_to_footprint_fallback = mapComponentFootprintsFromBoard(board, reference_to_component, components)

    logger.info(f"used components found: {len(components)}")
    logger.info(f"symbol definitions found: {len(symbol_map)}")
    logger.info(f"unique board footprints found: {len(footprint_map)}")
    logger.info(f"footprints with 3D models found: {len(model_paths)}")
    logger.info(f"components with 3D models found from board refs: {len(component_model_map)}")
    logger.info(f"board reference fallback mappings: {len(component_to_footprint_fallback)}")

    if not model_paths and localized_model_refs:
      logger.error(
        "no source 3D models could be resolved, but the board already points "
        f"to {localized_model_refs} localized 3D refs under {LOCAL_LIBS_DIR}/3d; "
        "the source project copy is likely already localized and missing its "
        "original 3D model paths"
      )

    if not components:
      logger.error("no used components detected in schematic; aborting")
      return

    logger.info("2) Export per-component files to components/")

    exported, export_stats, component_name_map = exportComponentFiles(
      root,
      components,
      symbol_map,
      footprint_map,
      footprint_name_map,
      model_paths,
      component_model_map,
      component_to_footprint_fallback,
      logger,
    )

    logger.info(
      "export summary: "
      f"symbols={export_stats['exported_symbol']}, "
      f"footprints={export_stats['exported_footprint']}, "
      f"models={export_stats['exported_model']}"
    )
    logger.info(f"step source fallback from footprint libraries: {export_stats['step_from_fp_library']}")
    logger.info(
      "model source from board-reference mapping: "
      f"{export_stats['model_from_component_board_map']}"
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
    buildCombinedSymbolLibrary(root, logger)

    logger.info(f"4) Build {LOCAL_LIBS_DIR}/{LOCAL_LIB}.pretty from components/")
    buildCombinedFootprintLibrary(root, exported)
    extra_fp_count = exportAllBoardFootprintsToLocalLibrary(root, footprint_map, footprint_name_map)
    logger.info(f"all board footprints exported to localLib.pretty: {extra_fp_count}")

    logger.info(f"5) Build {LOCAL_LIBS_DIR}/3d from components/")
    buildCombined3dDirectory(root, exported, board)

    logger.info("6) Write project library tables")
    ensureTables(root)

    if REWRITE_SCHEMATIC_REFS:
      logger.info("7) Rewrite schematic library references")
      rewrite_stats = rewriteSchematic(sch_path, component_name_map, footprint_name_map)
      logger.info(
        "schematic rewritten: "
        f"lib_ids={rewrite_stats['lib_ids']}, "
        f"footprints={rewrite_stats['footprints']}, "
        f"lib_symbols={rewrite_stats['lib_symbols']}, "
        f"cache_footprints={rewrite_stats['cache_footprints']}, "
        f"cache_sanitized={rewrite_stats['cache_sanitized']}"
      )
      if SYNC_SCHEMATIC_LIB_SYMBOLS:
        cache_count = syncSchematicLibSymbolsFromLocalLibrary(
          root,
          sch_path,
          set(exported.keys()),
          logger,
        )
        logger.info(f"schematic lib_symbols cache synced from localLib: {cache_count}")
      else:
        logger.info("schematic lib_symbols cache sync skipped (safe mode)")
    else:
      logger.info("7) Rewrite schematic library references (skipped in stable mode)")

    logger.info("8) Rewrite board footprint and 3D references")
    logModelRefSummary(logger, root, pcb_path, "before")
    mem_updates = rewriteBoard(board, root, footprint_name_map, reference_to_component, component_name_map)
    logger.info(f"in-memory 3D model entries rewritten: {mem_updates}")
    board.Save(str(pcb_path))
    pretty_models, pretty_transitions = rewritePrettyModelPaths(root)
    logger.info(f"localLib.pretty 3D model entries rewritten: {pretty_models}")
    changed_models, pcb_transitions = rewriteBoardModelPathsInFile(pcb_path, root)
    logger.info(f"pcb 3D model entries rewritten in file: {changed_models}")
    logLibraryTransitions(logger, "3D library transitions (pretty):", pretty_transitions)
    logLibraryTransitions(logger, "3D library transitions (pcb):", pcb_transitions)
    logModelRefSummary(logger, root, pcb_path, "after")
    logNonKiprjmod3dErrors(logger, root, pcb_path)

    logger.info(
      "SUMMARY "
      f"symbols={export_stats['exported_symbol']} "
      f"footprints={export_stats['exported_footprint']} "
      f"models={export_stats['exported_model']} "
      f"pretty3d={pretty_models} "
      f"pcb3d={changed_models}"
    )

    pcbnew.Refresh()
    logger.info(f"DONE (components + {LOCAL_LIBS_DIR}/)")


KiCadLocalizerPlugin().register()
