# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Local download folder layout. Kept free of QGIS/Qt imports so it can be unit
tested with plain pytest.

    <download dir>/<product>/<resolution>/<year>/            the dataset dir, e.g. lidar_composite_dtm/1m/2022
    <dataset dir>/<TILE>/...     rasters / point clouds / photos from the tile's zip (or its files)
    <dataset dir>/<TILE>.zip     the zip itself (optional: may be deleted after extraction)
    <dataset dir>/<SQ10>/...     files covering a whole 10 km square (e.g. ST16), shared by its four tiles

Point clouds and photos have no resolution level (<product>/<year>). See
api.dataset_folder. Most functions below take the dataset dir. Layouts from
earlier versions are moved into this one by migrate.py.
"""

import glob
import json
import os
import shutil
import zipfile

from . import api

# Tile names in the OSGB 5km grid, e.g. "SU12NW"
TILE_NAME_RE = api.TILE_RE
# 10 km squares, e.g. "ST16" (Welsh archive zips and some Scottish files cover one)
SQUARE_RE = api.SQUARE_10KM_RE

RASTER_PATTERNS = ('*.asc', '*.tif', '*.tiff', '*.ecw', '*.jp2')
POINT_CLOUD_PATTERNS = ('*.laz', '*.las')
PHOTO_PATTERNS = ('*.jpg', '*.jpeg')
# Composite products list the surveys merged into each tile (polygons with flown dates)
METADATA_PATTERNS = ('*_Metadata.gpkg', '*_metadata.gpkg')


def zip_path(download_dir, tile):
    return os.path.join(download_dir, f'{tile}.zip')


def extract_path(download_dir, tile):
    return os.path.join(download_dir, tile)


def scan_downloaded_tiles(download_dir):
    """Return sorted names of tiles that have a zip or extracted rasters."""
    if not os.path.isdir(download_dir):
        return []

    tiles = set()
    for f in os.listdir(download_dir):
        if f.lower().endswith('.zip'):
            tile_name = os.path.splitext(f)[0].upper()
            if TILE_NAME_RE.match(tile_name):
                tiles.add(tile_name)

    # Also count extracted tile folders, in case the zips were deleted. An empty
    # folder (e.g. left by a failed extraction) doesn't count.
    for d in os.listdir(download_dir):
        name = d.upper()
        path = os.path.join(download_dir, d)
        if name not in tiles and TILE_NAME_RE.match(name) and not _unfinished(path, name) and _data_in(path):
            tiles.add(name)
        elif SQUARE_RE.match(name) and _data_in(path):
            tiles.update(t for t in (name + q for q in ('NE', 'NW', 'SE', 'SW')) if _square_serves(path, t))

    return sorted(tiles)


# Per-file downloads (Wales, Scotland) mark each folder they fill: '.<TILE>.incomplete' while a tile's files
# are coming, '.<TILE>.done' once they all are. A 10 km square's folder serves the tiles marked done in it
# (a zip listed for one tile of the four doesn't count for the others).
def _marker(folder, tile, state):
    return os.path.join(folder, f'.{tile.upper()}.{state}')


def _unfinished(folder, tile):
    return os.path.exists(_marker(folder, tile, 'incomplete'))


def _square_serves(folder, tile):
    if os.path.exists(_marker(folder, tile, 'done')):
        return True
    # Downloaded before the markers (v0.10.0): the folder's files serve all four tiles
    marked = glob.glob(os.path.join(glob.escape(folder), '.*.done'))
    return not marked and not glob.glob(os.path.join(glob.escape(folder), '.*.incomplete'))


def start_tile_files(dataset_dir, tile, refs):
    """Note that a tile's files are being fetched (so a partly downloaded tile doesn't count)."""
    for folder in {file_folder(dataset_dir, ref) for ref in refs}:
        os.makedirs(folder, exist_ok=True)
        remove_quietly(_marker(folder, tile, 'done'))
        with open(_marker(folder, tile, 'incomplete'), 'w', encoding='ascii') as f:
            f.write('downloading\n')


def finish_tile_files(dataset_dir, tile, refs):
    """Note that all of a tile's files are here."""
    for folder in {file_folder(dataset_dir, ref) for ref in refs}:
        with open(_marker(folder, tile, 'done'), 'w', encoding='ascii') as f:
            f.write('done\n')
        remove_quietly(_marker(folder, tile, 'incomplete'))


def _data_in(folder):
    return any(glob.glob(os.path.join(folder, '**', pattern), recursive=True)
               for pattern in RASTER_PATTERNS + POINT_CLOUD_PATTERNS + PHOTO_PATTERNS)


def is_downloaded(download_dir, tile):
    """True if the tile's zip or its extracted data files are present (and not still being fetched)."""
    if os.path.exists(zip_path(download_dir, tile)):
        return True
    return not _unfinished(extract_path(download_dir, tile), tile) and bool(find_data_files(download_dir, tile))


def _find(download_dir, tile, patterns):
    """Files for a tile: in its own folder, and in its 10 km square's folder (files shared by four tiles)."""
    folders = [extract_path(download_dir, tile)]
    square = os.path.join(download_dir, tile[:4])
    if TILE_NAME_RE.match(tile.upper()) and os.path.isdir(square) and _square_serves(square, tile):
        folders.append(square)
    files = set()
    for path in folders:
        if not os.path.isdir(path):
            continue
        for pattern in patterns:
            files.update(glob.glob(os.path.join(path, '**', pattern), recursive=True))
    return sorted(files)


def find_raster_files(download_dir, tile):
    """Return raster files extracted for a tile (searches subfolders too)."""
    return _find(download_dir, tile, RASTER_PATTERNS)


def find_point_cloud_files(download_dir, tile):
    """Return point cloud files (.laz/.las) extracted for a tile."""
    return _find(download_dir, tile, POINT_CLOUD_PATTERNS)


def find_photo_files(download_dir, tile):
    """Return photos (.jpg) extracted for a tile, e.g. oblique aerial photography."""
    return _find(download_dir, tile, PHOTO_PATTERNS)


def find_metadata_files(download_dir, tile):
    """Return the survey metadata GeoPackages (e.g. SU12ne_DTM_1m_Metadata.gpkg) that come with a tile."""
    return _find(download_dir, tile, METADATA_PATTERNS)


def find_data_files(download_dir, tile):
    """Return all raster, point cloud and photo files extracted for a tile."""
    return _find(download_dir, tile, RASTER_PATTERNS + POINT_CLOUD_PATTERNS + PHOTO_PATTERNS)


def _holds_tiles(path):
    for name in os.listdir(path):
        stem, ext = os.path.splitext(name)
        if ext.lower() == '.zip' and TILE_NAME_RE.match(stem.upper()):
            return True
        if not ext and (TILE_NAME_RE.match(name.upper()) or SQUARE_RE.match(name.upper())) \
                and os.path.isdir(os.path.join(path, name)):
            return True
    return False


def dataset_dirs(download_dir):
    """Every folder under the download folder that holds tiles (zips or tile folders)."""
    if not os.path.isdir(download_dir):
        return []
    found = []
    for folder, subdirs, _ in os.walk(download_dir):
        if _holds_tiles(folder):
            found.append(folder)
        # Don't descend into tile (or 10 km square) folders themselves
        subdirs[:] = sorted(d for d in subdirs if not TILE_NAME_RE.match(d.upper()) and not SQUARE_RE.match(d.upper()))
    return found


def local_datasets(download_dir, product):
    """[(Dataset, dataset dir)] for the product's surveys found on disk."""
    base = os.path.join(download_dir, product)
    result = []
    for folder in dataset_dirs(base):
        parts = os.path.relpath(folder, base).split(os.sep)
        dataset = api.dataset_from_folder(product, parts)
        if dataset is not None:
            result.append((dataset, folder))
    return result


def folder_size(path):
    """Total size in bytes of the files under a folder."""
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:  # gone since the folder was listed
                pass
    return total


def downloaded_datasets(download_dir):
    """[(Dataset, folder, tiles)]: every survey in the download folder, in the Dataset menu's order. Quick (it
    doesn't measure the folders: download_summary does)."""
    if not os.path.isdir(download_dir):
        return []
    rows = []
    for product in sorted(os.listdir(download_dir)):
        if not os.path.isdir(os.path.join(download_dir, product)):
            continue
        for dataset, folder in local_datasets(download_dir, product):
            rows.append((dataset, folder, scan_downloaded_tiles(folder)))
    order = api.sorted_datasets([row[0] for row in rows])
    return sorted(rows, key=lambda row: (order.index(row[0]), row[1]))


def download_summary(download_dir):
    """[(Dataset, folder, tiles, bytes)]: every survey in the download folder with the space it takes, in the
    Dataset menu's order."""
    return [(dataset, folder, tiles, folder_size(folder))
            for dataset, folder, tiles in downloaded_datasets(download_dir)]


def free_space(path):
    """Free bytes on the drive holding path (or the nearest folder above it that exists), or None."""
    path = os.path.abspath(path)
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _inside(path, root):
    path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return path != root and os.path.commonpath([path, root]) == root


def delete_dataset(download_dir, folder):
    """Permanently delete one survey's folder (tiles, zips, mosaics...) from the download folder,
    then any folders above it left empty. Refuses anything that isn't a survey folder inside the download
    folder. Returns (bytes freed, [(path, error)] for anything that couldn't be deleted)."""
    if not _inside(folder, download_dir) or os.path.normcase(os.path.realpath(folder)) not in {
            os.path.normcase(os.path.realpath(d)) for d in dataset_dirs(download_dir)}:
        raise ValueError(f'not a survey folder in {download_dir}: {folder}')
    size = folder_size(folder)
    errors = []
    shutil.rmtree(folder, onerror=lambda function, path, info: errors.append((path, str(info[1]))))
    parent = os.path.dirname(folder)
    while _inside(parent, download_dir) and os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)
        parent = os.path.dirname(parent)
    return size - (folder_size(folder) if os.path.exists(folder) else 0), errors


# ------------------------------------------------------------------ survey information

SURVEY_INFO = 'survey.json'


def write_survey_info(dataset_dir, info):
    """Keep what's known about a downloaded survey (e.g. its licence statement) beside its tiles."""
    os.makedirs(dataset_dir, exist_ok=True)
    write_file_atomically(os.path.join(dataset_dir, SURVEY_INFO), json.dumps(info, indent=1).encode('utf-8'))


def read_survey_info(dataset_dir):
    """The survey information saved with a download, or {}."""
    try:
        with open(os.path.join(dataset_dir, SURVEY_INFO), encoding='utf-8') as f:
            info = json.load(f)
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ per-file downloads (Wales, Scotland)

def file_folder(dataset_dir, file_ref):
    """Where a downloaded file goes: its tile's folder, or its 10 km square's folder."""
    return os.path.join(dataset_dir, file_ref.square)


def _extracted_marker(folder, name):
    return os.path.join(folder, f'.{name}.extracted')


def file_done(dataset_dir, file_ref):
    """True if a file is already downloaded (at its listed size, when known), or a zip already extracted."""
    folder = file_folder(dataset_dir, file_ref)
    if file_ref.name.lower().endswith('.zip'):
        return os.path.exists(_extracted_marker(folder, file_ref.name))
    path = os.path.join(folder, file_ref.name)
    return os.path.exists(path) and (not file_ref.size or os.path.getsize(path) == file_ref.size)


def _check_room(zf, folder):
    """Raise OSError if the zip's contents won't fit on the drive holding folder."""
    needed = sum(m.file_size for m in zf.infolist())
    free = free_space(folder)
    if free is not None and needed > free:
        raise OSError(f'not enough disk space to extract it (needs {needed / 1e6:,.0f} MB, '
                      f'{free / 1e6:,.0f} MB free)')


def extract_file_zip(path):
    """Extract a downloaded zip beside itself (e.g. into its 10 km square's folder) and mark it done.
    A corrupt zip is deleted and zipfile.BadZipFile re-raised."""
    folder = os.path.dirname(path)
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            _check_room(zf, folder)
            zf.extractall(folder)  # zipfile drops '..' and drive / root parts of member names: stays in folder
    except zipfile.BadZipFile:
        remove_quietly(path)
        raise
    add_missing_prj(folder)
    with open(_extracted_marker(folder, os.path.basename(path)), 'w', encoding='ascii') as f:
        f.write('extracted\n')


def write_file_atomically(path, data):
    """Write bytes via a .part file so an interrupted write never leaves a truncated file."""
    part_path = path + '.part'
    try:
        with open(part_path, 'wb') as f:
            f.write(data)
        os.replace(part_path, path)
    except BaseException:
        remove_quietly(part_path)
        raise


# British National Grid (EPSG:27700) as an ESRI .prj, for grids that come without one
BNG_PRJ = ('PROJCS["British_National_Grid",GEOGCS["GCS_OSGB_1936",DATUM["D_OSGB_1936",SPHEROID["Airy_1830",'
           '6377563.396,299.3249646]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
           'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",400000.0],'
           'PARAMETER["False_Northing",-100000.0],PARAMETER["Central_Meridian",-2.0],'
           'PARAMETER["Scale_Factor",0.9996012717],PARAMETER["Latitude_Of_Origin",49.0],'
           'UNIT["Meter",1.0]]')


def add_missing_prj(folder):
    """Give ESRI ASCII grids without a .prj one for British National Grid (all Environment Agency data is
    in it; the river bathymetry grids come without). Returns the .prj files written."""
    written = []
    for path in glob.glob(os.path.join(folder, '**', '*.asc'), recursive=True):
        prj = os.path.splitext(path)[0] + '.prj'
        if not os.path.exists(prj) and not os.path.exists(os.path.splitext(path)[0] + '.PRJ'):
            with open(prj, 'w', encoding='ascii') as f:
                f.write(BNG_PRJ)
            written.append(prj)
    return written


def extract_tile(download_dir, tile):
    """Extract <dataset dir>/<TILE>.zip into <dataset dir>/<TILE>/.

    A corrupt zip is deleted (so the next attempt downloads it again) and
    zipfile.BadZipFile is re-raised.
    """
    path = zip_path(download_dir, tile)
    dest = extract_path(download_dir, tile)
    os.makedirs(dest, exist_ok=True)
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            _check_room(zf, dest)
            zf.extractall(dest)  # zipfile drops '..' and drive / root parts of member names: stays in dest
    except zipfile.BadZipFile:
        remove_quietly(path)
        raise
    add_missing_prj(dest)


def zip_fully_extracted(download_dir, tile):
    """True if every file in the tile's zip exists, at the same size, in its extracted folder."""
    dest = extract_path(download_dir, tile)
    try:
        with zipfile.ZipFile(zip_path(download_dir, tile), 'r') as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
    except (OSError, zipfile.BadZipFile):
        return False
    if not members:
        return False
    for member in members:
        extracted = os.path.join(dest, *member.filename.split('/'))
        if not os.path.isfile(extracted) or os.path.getsize(extracted) != member.file_size:
            return False
    return True


def find_redundant_zips(download_dir):
    """Split the tile zips in the download folder and its dataset sub-folders into (redundant, kept).

    Redundant zips are fully extracted, so deleting them loses nothing. Both
    are lists of (tile, zip path, size in bytes).
    """
    redundant, kept = [], []
    for folder in dataset_dirs(download_dir):
        for f in sorted(os.listdir(folder)):
            tile, ext = os.path.splitext(f)
            if ext.lower() != '.zip' or not TILE_NAME_RE.match(tile.upper()):
                continue
            path = os.path.join(folder, f)
            entry = (tile, path, os.path.getsize(path))
            (redundant if zip_fully_extracted(folder, tile) else kept).append(entry)
    return redundant, kept


def format_file_list(entries, limit=10, base_dir=None):
    """'  SU12NE.zip  (69 MB)' lines for (tile, path, size) entries, truncated after limit.

    Paths are shown relative to base_dir if given (so dataset sub-folders are visible).
    """
    def name(path):
        return os.path.relpath(path, base_dir) if base_dir else os.path.basename(path)
    lines = [f"  {name(path)}  ({size / 1e6:.0f} MB)" for _, path, size in entries[:limit]]
    if len(entries) > limit:
        lines.append(f"  ... and {len(entries) - limit} more")
    return "\n".join(lines)


def remove_quietly(path):
    """Delete a file if it's there (a missing file is no error, nor is one in use: it's left for next time)."""
    try:
        os.remove(path)
    except OSError:
        pass
