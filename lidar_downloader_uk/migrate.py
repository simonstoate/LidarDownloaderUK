# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Move downloads from earlier folder layouts into the current one.

Kept free of QGIS/Qt imports so it can be unit tested with plain pytest.

Earlier layouts, all under the download folder:

    extracted/<TILE>/ and <TILE>.zip                 Composite DTM 2022 1m (v0.1 - v0.5)
    <product>_<year>_<res>m/extracted/<TILE>/ ...    other datasets (v0.5), e.g. ..._2009_0.2m
    <product>_<year>/extracted/<TILE>/ ...           point clouds and photos (v0.5)
    <product>_latest[_<res>m]/extracted/<TILE>/ ...  "latest available" downloads (v0.5)

Current layout: <product>/<res>/<year>/<TILE>/ (see api.dataset_folder).

Moves are renames within the download folder (never copy-then-delete, so a move
that fails - e.g. because a file is open - changes nothing): nothing is copied or deleted,
except the photo_locations.gpkg files the plugin generates (they hold the old
photo paths and are rebuilt on the next load). Destinations that already exist
are never overwritten. VRT files under the download folder that point at moved
tiles are rewritten to the new locations.
"""

import os
import re
from dataclasses import dataclass

from . import api
from .storage import TILE_NAME_RE

LEGACY_FOLDER_RE = re.compile(r'^(?P<product>[a-z_]+?)_(?P<year>\d{4}|latest)(?:_(?P<res>\d+(?:\.\d+)?)m)?$')
# Survey dates in file names, e.g. Obliques_S23_016_20230121_141845.jpg, VOM_..._20190425_20190429.tif
DATE_RE = re.compile(r'(?<!\d)((?:19[89]|20[0-4])\d)(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)')
# ...or a 6-digit date between underscores, e.g. Obliques_S20_060_200217_102520.jpg (17 Feb 2020)
SHORT_DATE_RE = re.compile(r'(?<=_)(\d{2})(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?=_)')
GENERATED_FILES = ('photo_locations.gpkg',)


@dataclass(frozen=True)
class Move:
    src: str
    dst: str
    tile: str
    dataset: api.Dataset
    is_zip: bool = False


def _year_from_file_names(folder):
    """The survey year if the data files' names agree on one, else None."""
    years, short_years = set(), set()
    for _, _, files in os.walk(folder):
        for name in files:
            if name in GENERATED_FILES:
                continue
            years.update(m.group(1) for m in DATE_RE.finditer(name))
            short_years.update('20' + m.group(1) for m in SHORT_DATE_RE.finditer(name))
    if not years:
        years = short_years
    return years.pop() if len(years) == 1 else None


def _tiles_in(extracted_dir, zips_dir):
    tiles = {}
    if os.path.isdir(extracted_dir):
        for name in os.listdir(extracted_dir):
            if TILE_NAME_RE.match(name.upper()) and os.path.isdir(os.path.join(extracted_dir, name)):
                tiles.setdefault(name.upper(), {})['dir'] = os.path.join(extracted_dir, name)
    if os.path.isdir(zips_dir):
        for name in os.listdir(zips_dir):
            stem, ext = os.path.splitext(name)
            if ext.lower() == '.zip' and TILE_NAME_RE.match(stem.upper()):
                tiles.setdefault(stem.upper(), {})['zip'] = os.path.join(zips_dir, name)
    return tiles


def _legacy_sources(root):
    """(folder holding zips, folder holding extracted tiles, product, year or 'latest', resolution) per old layout."""
    yield root, os.path.join(root, 'extracted'), api.PRODUCT, api.YEAR, api.RESOLUTION
    for name in sorted(os.listdir(root)):
        match = LEGACY_FOLDER_RE.match(name)
        folder = os.path.join(root, name)
        if not match or not os.path.isdir(folder) or not api.is_supported_product(match.group('product')):
            continue
        yield (folder, os.path.join(folder, 'extracted'), match.group('product'), match.group('year'),
               match.group('res') or 'NaN')


def plan(root):
    """Work out the moves needed. Returns (moves, skipped) where skipped is [(path, reason)]."""
    moves, skipped = [], []
    if not os.path.isdir(root):
        return moves, skipped
    for zips_dir, extracted_dir, product, year, resolution in _legacy_sources(root):
        for tile, parts in sorted(_tiles_in(extracted_dir, zips_dir).items()):
            tile_year = year
            if year == api.LATEST:
                tile_year = _year_from_file_names(parts['dir']) if 'dir' in parts else None
                if tile_year is None:
                    skipped.extend((p, "survey year unknown (from a 'latest available' download)")
                                   for p in parts.values())
                    continue
            dataset = api.Dataset(product, tile_year, resolution)
            target = os.path.join(root, api.dataset_folder(dataset))
            for key, src in parts.items():
                dst = os.path.join(target, tile if key == 'dir' else f'{tile}.zip')
                if os.path.exists(dst):
                    skipped.append((src, f'already exists: {dst}'))
                else:
                    moves.append(Move(src, dst, tile, dataset, is_zip=(key == 'zip')))
    return moves, skipped


def summary(moves):
    """{Dataset: number of tiles} for the moves."""
    counts = {}
    for move in moves:
        counts.setdefault(move.dataset, set()).add(move.tile)
    return {d: len(t) for d, t in counts.items()}


def apply(root, moves):
    """Carry out the moves. Returns (done, errors); tidies up empty old folders afterwards."""
    done, errors = [], []
    for move in moves:
        try:
            os.makedirs(os.path.dirname(move.dst), exist_ok=True)
            if os.path.exists(move.dst):
                raise OSError(f'{move.dst} already exists')
            os.rename(move.src, move.dst)  # a rename only: fails cleanly rather than half-copying
            if not move.is_zip:
                for name in GENERATED_FILES:
                    generated = os.path.join(move.dst, name)
                    if os.path.exists(generated):
                        os.remove(generated)
            done.append(move)
        except OSError as e:
            errors.append((move.src, str(e)))
    for zips_dir, extracted_dir, *_ in list(_legacy_sources(root)):
        for folder in (extracted_dir, zips_dir):
            if folder != root and os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
    return done, errors


def _normalised(path):
    return os.path.normcase(os.path.normpath(path))


def new_path(path, moves):
    """Where a file under a moved tile folder (or a moved zip) is now, or None if it didn't move."""
    target = _normalised(path)
    for move in moves:
        src = _normalised(move.src)
        if target == src:
            return move.dst
        if not move.is_zip and target.startswith(src + os.sep):
            return os.path.join(move.dst, os.path.normpath(path)[len(os.path.normpath(move.src)) + 1:])
    return None


SOURCE_RE = re.compile(r'(<SourceFilename)([^>]*)>([^<]+)(</SourceFilename>)')


def rewrite_vrts(root, moves):
    """Point VRT files under root at moved tiles. Returns the paths of the VRTs changed."""
    changed = []
    for folder, _, files in os.walk(root):
        for name in files:
            if not name.lower().endswith('.vrt'):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding='utf-8') as f:
                text = f.read()

            def replace(match, vrt_dir=folder):
                attrs, source = match.group(2), match.group(3)
                relative = 'relativeToVRT="1"' in attrs
                absolute = os.path.normpath(os.path.join(vrt_dir, source) if relative else source)
                moved = new_path(absolute, moves)
                if moved is None:
                    return match.group(0)
                if relative:
                    try:
                        source = os.path.relpath(moved, vrt_dir).replace(os.sep, '/')
                    except ValueError:  # different drive: fall back to an absolute path
                        attrs, source = attrs.replace('relativeToVRT="1"', 'relativeToVRT="0"'), moved
                else:
                    source = moved
                return f'{match.group(1)}{attrs}>{source}{match.group(4)}'

            new_text = SOURCE_RE.sub(replace, text)
            if new_text != text:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_text)
                changed.append(path)
    return changed
