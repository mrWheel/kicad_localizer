#!/usr/bin/env python3

import os
import sys
import argparse
import logging
from pathlib import Path


# --- Configure logging

def setupLogging(debug):

  level = logging.DEBUG if debug else logging.INFO

  logging.basicConfig(
    level=level,
    format="%(levelname)s: %(message)s"
  )


class ParseError(Exception):

  def __init__(self, message, position=None):

    super().__init__(message)
    self.position = position


def formatPosition(text, pos):

  if pos is None:
    return ""

  line = 1
  col = 1

  for idx, ch in enumerate(text):
    if idx >= pos:
      break

    if ch == "\n":
      line += 1
      col = 1
    else:
      col += 1

  return f" (line {line}, col {col})"


def hasChildList(node, head):

  if not isinstance(node, list):
    return False

  for child in node:
    if isinstance(child, list) and child and child[0] == head:
      return True

  return False


def ensureRootIsSchematic(tree):

  if not isinstance(tree, list) or not tree:
    raise ParseError("Error: Root expression is empty or invalid")

  if tree[0] != "kicad_sch":
    raise ParseError(f"Error: Root node must be 'kicad_sch', got '{tree[0]}'")


class KiCadParser:

  def __init__(self, text):

    self.text = text
    self.pos = 0
    self.length = len(text)


  def peek(self):

    if self.pos < self.length:
      return self.text[self.pos]

    return None


  def advance(self):

    ch = self.peek()
    self.pos += 1
    return ch


  def skipWhitespace(self):

    while True:

      ch = self.peek()

      if ch is None or not ch.isspace():
        return

      self.advance()


  def parse(self):

    self.skipWhitespace()

    if self.peek() != '(':
      raise ParseError("Error: File must start with '('", self.pos)

    parsed = self.parseList()

    self.skipWhitespace()
    if self.peek() is not None:
      raise ParseError("Error: Trailing data after root expression", self.pos)

    return parsed


  def parseList(self):

    items = []

    self.advance()

    while True:

      self.skipWhitespace()

      ch = self.peek()

      if ch is None:
        raise ParseError("Error: Unexpected EOF", self.pos)

      if ch == ')':
        self.advance()
        return items

      if ch == '(':
        items.append(self.parseList())
      else:
        items.append(self.parseAtom())


  def parseAtom(self):

    ch = self.peek()

    # --- String
    if ch == '"':

      self.advance()
      value = ""

      while True:

        ch = self.peek()

        if ch is None:
          raise ParseError("Error: Unterminated string", self.pos)

        if ch == '"':
          self.advance()
          return value

        value += self.advance()

    # --- Symbol / number
    token = ""

    while True:

      ch = self.peek()

      if ch is None or ch.isspace() or ch in ['(', ')']:
        break

      token += self.advance()

    if token == "":
      raise ParseError("Error: Empty token", self.pos)

    return token


# --- Extraction helpers

def extractPlacedSymbols(tree):

  symbols = []

  def walk(node, insideLibSymbols=False):

    if not isinstance(node, list) or not node:
      return

    nowInsideLibSymbols = insideLibSymbols or node[0] == "lib_symbols"

    if not nowInsideLibSymbols and node[0] == "symbol" and hasChildList(node, "lib_id"):
      symbols.append(node)

    for child in node:
      walk(child, nowInsideLibSymbols)

  walk(tree)
  return symbols


def extractWires(tree):

  wires = []

  def walk(node):

    if isinstance(node, list) and node:

      if node[0] == "wire":
        wires.append(node)

      for child in node:
        walk(child)

  walk(tree)
  return wires


def extractSheets(tree):

  sheets = []

  def walk(node):

    if isinstance(node, list) and node:

      if node[0] == "sheet":

        for item in node:

          if isinstance(item, list) and item and item[0] == "property":

            if len(item) > 2 and item[1] == "Sheetfile":
              sheets.append(item[2])

      for child in node:
        walk(child)

  walk(tree)
  return sheets


def getProperty(symbol, name):

  for item in symbol:

    if isinstance(item, list) and item and item[0] == "property":

      if len(item) > 2 and item[1] == name:
        return item[2]

  return None


def getLibId(symbol):

  for item in symbol:

    if isinstance(item, list) and item and item[0] == "lib_id" and len(item) > 1:
      return item[1]

  return None


def getChildAtom(node, head):

  if not isinstance(node, list):
    return None

  for child in node:
    if isinstance(child, list) and child and child[0] == head and len(child) > 1 and isinstance(child[1], str):
      return child[1]

  return None


def extractSymbolInstanceAssignments(tree):

  assignments = []

  def walk(node, inSymbolInstances=False):

    if not isinstance(node, list) or not node:
      return

    nowInSymbolInstances = inSymbolInstances or node[0] == "symbol_instances"

    if nowInSymbolInstances and node[0] == "path":
      ref = getChildAtom(node, "reference")
      footprint = getChildAtom(node, "footprint")
      pathValue = node[1] if len(node) > 1 and isinstance(node[1], str) else ""

      assignments.append({
        "reference": ref,
        "footprint": footprint,
        "path": pathValue,
      })

    for child in node:
      walk(child, nowInSymbolInstances)

  walk(tree)
  return assignments


def normalizeLibraryQualifiedName(rawValue):

  if not rawValue:
    return rawValue

  if ":" in rawValue:
    return rawValue.split(":", 1)[1]

  return rawValue


# --- Load all sheets recursively and keep sheet path context

def loadAllSheets(mainFile, visited=None, rootDir=None, debug=False):

  if visited is None:
    visited = set()

  fullPath = os.path.abspath(mainFile)

  if rootDir is None:
    rootDir = os.path.dirname(fullPath)

  results = []

  if fullPath in visited:
    logging.debug(f"Duplicate sheet skipped: {fullPath}")
    return results

  visited.add(fullPath)

  text = ""

  try:

    with open(fullPath, "r", encoding="utf-8") as fileHandle:
      text = fileHandle.read()

    parser = KiCadParser(text)
    tree = parser.parse()
    ensureRootIsSchematic(tree)

  except ParseError as error:

    pos_text = formatPosition(text, error.position)
    logging.error(f"{fullPath}: {error}{pos_text}")
    return results

  except Exception as error:

    logging.error(f"{fullPath}: {error}")
    return results

  relPath = os.path.relpath(fullPath, rootDir)

  placed = extractPlacedSymbols(tree)
  if debug:
    logging.debug(f"Loaded sheet: {fullPath}")
    logging.debug(f"  Placed symbols in sheet: {len(placed)}")

  results.append({
    "sheetPath": relPath,
    "fullPath": fullPath,
    "tree": tree
  })

  baseDir = os.path.dirname(fullPath)

  for sheetFile in extractSheets(tree):

    subPath = os.path.abspath(os.path.join(baseDir, sheetFile))

    if not os.path.exists(subPath):
      logging.error(f"Missing sheet file: {subPath}")
      continue

    results.extend(loadAllSheets(subPath, visited, rootDir, debug))

  return results


# --- Mode 1: extensive syntax checks

def runSyntaxCheck(sheetEntries):

  if not sheetEntries:
    return 1

  totalPlaced = 0
  totalWires = 0

  for entry in sheetEntries:
    placed = extractPlacedSymbols(entry["tree"])
    wires = extractWires(entry["tree"])
    totalPlaced += len(placed)
    totalWires += len(wires)
    logging.debug(
      f"Syntax detail [{entry['sheetPath']}]: placed_symbols={len(placed)}, wires={len(wires)}"
    )

  logging.info(
    f"Syntax OK: sheets={len(sheetEntries)}, placed_symbols={totalPlaced}, wires={totalWires}"
  )
  return 0


# --- Mode 2: compare file1 with file2

def runDiff(sheetEntries1, sheetEntries2, strictDiff=False, file2MainPath=None):

  def buildSymbolMap(sheetEntries):

    mapping = {}

    for entry in sheetEntries:

      sheetPath = entry["sheetPath"]

      for symbol in extractPlacedSymbols(entry["tree"]):

        ref = getProperty(symbol, "Reference")
        if not ref:
          continue

        key = f"{sheetPath}::{ref}"

        if key in mapping:
          logging.debug(f"Duplicate placed symbol key in diff: {key}")

        libId = getLibId(symbol)
        mapping[key] = {
          "sheet": sheetPath,
          "ref": ref,
          "value": getProperty(symbol, "Value"),
          "footprint": (
            getProperty(symbol, "Footprint")
            if strictDiff
            else normalizeLibraryQualifiedName(getProperty(symbol, "Footprint"))
          ),
          "lib": libId if strictDiff else None,
        }

    return mapping


  map1 = buildSymbolMap(sheetEntries1)
  map2 = buildSymbolMap(sheetEntries2)
  allKeys = set(map1.keys()) | set(map2.keys())

  # In normal diff mode, explicitly report non-localized symbols in file2.
  if not strictDiff:
    nonLocalCount = 0

    for entry in sheetEntries2:
      sheetPath = entry["sheetPath"]

      for symbol in extractPlacedSymbols(entry["tree"]):
        ref = getProperty(symbol, "Reference") or "?"
        value = getProperty(symbol, "Value") or "?"
        libId = getLibId(symbol) or ""

        if not libId:
          continue

        # power:* symbols are intentionally not localized by the plugin.
        if libId.startswith("power:"):
          continue

        if not libId.startswith("localLib:"):
          logging.error(
            f"Not localized in file2: [{sheetPath}] {ref} "
            f"{{'value': {repr(value)}, 'lib': {repr(libId)}}}"
          )
          nonLocalCount += 1

    if nonLocalCount == 0:
      logging.info("All non-power symbols in file2 reference localLib")

    footprintIssues = validateFootprintAssignments(sheetEntries2, file2MainPath)
    if footprintIssues == 0:
      logging.info("Footprint assignments in file2 are consistent with localLib")

  changesFound = 0

  for key in sorted(allKeys):

    if key not in map1:
      item = map2[key]
      logging.info(
        f"Added: [{item['sheet']}] {item['ref']} "
        f"{{'value': {repr(item['value'])}, 'footprint': {repr(item['footprint'])}}}"
      )
      changesFound += 1
      continue

    if key not in map2:
      item = map1[key]
      logging.info(
        f"Removed: [{item['sheet']}] {item['ref']} "
        f"{{'value': {repr(item['value'])}, 'footprint': {repr(item['footprint'])}}}"
      )
      changesFound += 1
      continue

    old = map1[key]
    new = map2[key]

    if old["value"] != new["value"] or old["footprint"] != new["footprint"]:
      if strictDiff:
        logging.info(
          f"Changed: [{old['sheet']}] {old['ref']} "
          f"{{'value': {repr(old['value'])}, 'lib': {repr(old['lib'])}, 'footprint': {repr(old['footprint'])}}} -> "
          f"{{'value': {repr(new['value'])}, 'lib': {repr(new['lib'])}, 'footprint': {repr(new['footprint'])}}}"
        )
      else:
        logging.info(
          f"Changed: [{old['sheet']}] {old['ref']} "
          f"{{'value': {repr(old['value'])}, 'footprint': {repr(old['footprint'])}}} -> "
          f"{{'value': {repr(new['value'])}, 'footprint': {repr(new['footprint'])}}}"
        )
      changesFound += 1

  if changesFound == 0:
    logging.info("No differences found")

  return 0


def validateFootprintAssignments(sheetEntries, fileMainPath):

  issues = 0

  localFootprints = None
  if fileMainPath:
    root = Path(fileMainPath).resolve().parent
    localPrettyDir = root / "localLibs" / "localLib.pretty"
    if localPrettyDir.exists():
      localFootprints = {p.stem for p in localPrettyDir.glob("*.kicad_mod") if p.is_file()}
    else:
      logging.warning(f"local footprint library not found: {localPrettyDir}")

  for entry in sheetEntries:
    sheetPath = entry["sheetPath"]
    tree = entry["tree"]

    symbolByRef = {}

    for symbol in extractPlacedSymbols(tree):
      ref = getProperty(symbol, "Reference")
      if not ref:
        continue

      libId = getLibId(symbol) or ""
      footprint = getProperty(symbol, "Footprint") or ""
      symbolByRef[ref] = footprint

      if libId.startswith("power:"):
        continue

      if footprint and not footprint.startswith("localLib:"):
        logging.error(
          f"Footprint assignment not localized: [{sheetPath}] {ref} "
          f"uses {repr(footprint)}"
        )
        issues += 1

      if footprint.startswith("localLib:") and localFootprints is not None:
        fpName = footprint.split(":", 1)[1]
        if fpName not in localFootprints:
          logging.error(
            f"Footprint missing in localLib.pretty: [{sheetPath}] {ref} "
            f"uses {repr(footprint)}"
          )
          issues += 1

    for item in extractSymbolInstanceAssignments(tree):
      ref = item.get("reference") or "?"
      footprint = item.get("footprint") or ""

      if footprint and not footprint.startswith("localLib:"):
        logging.error(
          f"symbol_instances footprint not localized: [{sheetPath}] {ref} "
          f"uses {repr(footprint)}"
        )
        issues += 1

      if footprint.startswith("localLib:") and localFootprints is not None:
        fpName = footprint.split(":", 1)[1]
        if fpName not in localFootprints:
          logging.error(
            f"symbol_instances footprint missing in localLib.pretty: [{sheetPath}] {ref} "
            f"uses {repr(footprint)}"
          )
          issues += 1

      symbolFootprint = symbolByRef.get(ref)
      if symbolFootprint and footprint and symbolFootprint != footprint:
        logging.error(
          f"Footprint mismatch symbol vs symbol_instances: [{sheetPath}] {ref} "
          f"symbol={repr(symbolFootprint)} vs symbol_instances={repr(footprint)}"
        )
        issues += 1

  return issues


# --- Mode 3: netlist checks

def runNetlistCheck(sheetEntries):

  if not sheetEntries:
    return 1

  refs = {}
  errors = 0
  warnings = 0
  totalSymbols = 0
  totalWires = 0

  for entry in sheetEntries:
    sheetPath = entry["sheetPath"]
    symbols = extractPlacedSymbols(entry["tree"])
    wires = extractWires(entry["tree"])

    totalSymbols += len(symbols)
    totalWires += len(wires)

    for symbol in symbols:
      ref = getProperty(symbol, "Reference")
      value = getProperty(symbol, "Value")
      libId = getLibId(symbol)

      if not ref:
        logging.error(f"[{sheetPath}] Symbol without Reference")
        errors += 1
      else:
        if ref in refs:
          logging.error(
            f"[{sheetPath}] Duplicate reference {ref} (first seen in {refs[ref]})"
          )
          errors += 1
        else:
          refs[ref] = sheetPath

      if not value:
        logging.warning(f"[{sheetPath}] {ref or '?'} missing Value")
        warnings += 1

      if not libId:
        logging.error(f"[{sheetPath}] {ref or '?'} missing lib_id")
        errors += 1

    if len(wires) == 0:
      logging.warning(f"[{sheetPath}] No wires found")
      warnings += 1

  logging.info(
    f"Netlist checked: sheets={len(sheetEntries)}, symbols={totalSymbols}, "
    f"wires={totalWires}, errors={errors}, warnings={warnings}"
  )

  return 1 if errors > 0 else 0


# --- CLI

def main():

  parser = argparse.ArgumentParser(description="KiCad multi-sheet parser / diff / netlist tool")

  parser.add_argument("--debug", action="store_true", help="Enable debug logging")
  parser.add_argument("--netlist", action="store_true", help="Run netlist checks on file1 (and sub-sheets)")
  parser.add_argument("--strict-diff", action="store_true", help="Diff mode: compare raw values including library prefixes")
  parser.add_argument("file1", help="Main .kicad_sch file")
  parser.add_argument("file2", nargs="?", help="Second main .kicad_sch file (diff mode)")

  args = parser.parse_args()

  if args.netlist and args.file2:
    parser.error("--netlist mode accepts only file1")

  if args.strict_diff and not args.file2:
    parser.error("--strict-diff requires file2 (diff mode)")

  if args.strict_diff and args.netlist:
    parser.error("--strict-diff cannot be combined with --netlist")

  setupLogging(args.debug)

  # Mode 3: netlist checks
  if args.netlist:
    sheets = loadAllSheets(args.file1, debug=args.debug)
    return runNetlistCheck(sheets)

  # Mode 2: diff mode
  if args.file2:
    sheets1 = loadAllSheets(args.file1, debug=args.debug)
    sheets2 = loadAllSheets(args.file2, debug=args.debug)
    if not sheets1 or not sheets2:
      return 1
    return runDiff(
      sheets1,
      sheets2,
      strictDiff=args.strict_diff,
      file2MainPath=args.file2,
    )

  # Mode 1: extensive syntax check
  sheets = loadAllSheets(args.file1, debug=args.debug)
  return runSyntaxCheck(sheets)


if __name__ == "__main__":
  sys.exit(main())
