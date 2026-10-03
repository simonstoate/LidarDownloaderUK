# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Wales and Scotland: where their LIDAR is listed and how it maps onto the plugin's 5 km tiles.

The Environment Agency (England) serves a zip per 5 km tile (api.py). Wales and Scotland serve
files for 1 km, 5 km or 10 km squares, found through their own catalogues:

- Wales, DataMapWales (Welsh Government, Natural Resources Wales): WFS tile catalogues in EPSG:27700.
  The 2020-22 national survey has a 1 m DTM / DSM GeoTIFF per 1 km square; the archive (1998-2015) a
  zip per 10 km square and survey.
- Scotland, the Scottish Remote Sensing Portal (Scottish Government, JNCC): a catalogue API searched
  by footprint (WKT in WGS84), listing each product's file on the portal's S3 bucket.

Kept free of QGIS/Qt imports so it can be unit tested with plain pytest; tasks.py makes the requests.
"""

import os
import re
from urllib.parse import quote

from . import api

# ------------------------------------------------------------------ which sources cover a 100 km square

WALES_SQUARES = {'SH', 'SJ', 'SM', 'SN', 'SO', 'SR', 'SS', 'ST'}
EA_NORTHERN_SQUARES = {'NT', 'NU', 'NX', 'NY', 'NZ'}


def sources_for_square(square):
    """The sources worth asking about a 100 km square such as 'SU': {'ea', 'wales', 'scotland'}."""
    square = (square or '').upper()
    found = set()
    if square[:1] in ('S', 'T') or square in EA_NORTHERN_SQUARES:
        found.add('ea')
    if square in WALES_SQUARES:
        found.add('wales')
    if square[:1] in ('N', 'H'):
        found.add('scotland')
    return found


def merge_offerings(offerings):
    """One Offering per dataset and tile, with all their files (as several files can list the same tile)."""
    merged = {}
    for offering in offerings:
        key = (offering.dataset, offering.tile)
        if key not in merged:
            merged[key] = offering
            continue
        known = merged[key]
        urls = {f.url for f in known.files}
        files = known.files + tuple(f for f in offering.files if f.url not in urls)
        merged[key] = api.Offering(known.dataset, known.tile, known.tile_id, known.url, files,
                                   known.rights or offering.rights)
    return list(merged.values())


# ------------------------------------------------------------------ Wales

WALES_WFS = 'https://datamap.gov.wales/geoserver/ows'
WALES_NATIONAL_LAYER = 'geonode:welsh_government_lidar_tile_catalogue_2020_2023'
WALES_ARCHIVE_LAYER = 'geonode:nrw_lidar_tile_catalogue_archive'
WALES_NATIONAL_YEAR = '2020-2022'
WALES_PORTAL = 'https://datamap.gov.wales/maps/lidar-data-download/'

# Results asked for per request (DataMapWales' WFS, the Scottish catalogue); longer lists are fetched in pages
WALES_PAGE = 10000
SCOTLAND_PAGE = 5000


def wales_url(layer, bbox, count=WALES_PAGE, start=0):
    """WFS GetFeature URL for the catalogue features in a British National Grid box (xmin, ymin, xmax, ymax),
    one page of count features from start."""
    xmin, ymin, xmax, ymax = (int(round(v)) for v in bbox)
    return (f'{WALES_WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames={quote(layer)}'
            f'&outputFormat=application/json&count={count}&startIndex={start}'
            f'&bbox={xmin},{ymin},{xmax},{ymax},EPSG:27700')


def _https(link):
    link = (link or '').strip()
    if not link:
        return ''
    return link if link.startswith('https://') else 'https://' + link.split('://', 1)[-1]


def parse_wales_national(data):
    """Offerings from the 2020-22 national survey catalogue: a DTM and a DSM GeoTIFF per 1 km square."""
    offerings = []
    for feature in (data or {}).get('features', []):
        props = feature.get('properties') or {}
        found = api.square_tiles(props.get('british_gr'))
        if not found:
            continue
        folder, tiles = found
        for product, field in (('wales_lidar_dtm', 'dtm_link'), ('wales_lidar_dsm', 'dsm_link')):
            url = _https(props.get(field))
            if not api.is_trusted_url(url):
                continue
            dataset = api.Dataset(product, WALES_NATIONAL_YEAR, '1')
            ref = api.FileRef(url, os.path.basename(url), folder)
            offerings += [api.Offering(dataset, tile, tile, '', (ref,)) for tile in tiles]
    return merge_offerings(offerings)


def _archive_year(value):
    year = str(value or '').strip()
    return year if api.YEAR_RE.match(year) else ''


def _archive_resolution(value):
    try:
        number = float(str(value).strip())
    except ValueError:
        return ''
    return f'{number:g}' if number > 0 else ''


def parse_wales_archive(data):
    """Offerings from the archive catalogue: rows per 1 km square, each pointing at a zip for its 10 km square."""
    offerings = []
    for feature in (data or {}).get('features', []):
        props = feature.get('properties') or {}
        found = api.square_tiles(props.get('gb_ng'))
        year, resolution = _archive_year(props.get('year')), _archive_resolution(props.get('resolution'))
        if not found or not year or not resolution:
            continue
        _, tiles = found
        square = str(props.get('_10k') or '').strip().upper()
        for product, field in (('wales_lidar_archive_dtm', 'dtm_url'), ('wales_lidar_archive_dsm', 'dsm_url')):
            url = _https(props.get(field))
            if not api.is_trusted_url(url):
                continue
            zip_square = square if api.SQUARE_10KM_RE.match(square) else _square_in_name(url)
            if not zip_square:
                continue
            dataset = api.Dataset(product, year, resolution)
            ref = api.FileRef(url, os.path.basename(url), zip_square)
            offerings += [api.Offering(dataset, tile, tile, '', (ref,)) for tile in tiles]
    return merge_offerings(offerings)


def _square_in_name(url):
    match = re.search(r'_([A-HJ-Z]{2}\d{2})_', os.path.basename(url).upper())
    return match.group(1) if match else ''


# ------------------------------------------------------------------ Scotland

SCOTLAND_API = 'https://api.remotesensing.data.gov.scot'
SCOTLAND_COLLECTIONS_URL = SCOTLAND_API + '/search/collection/scotland-gov/*'
SCOTLAND_SEARCH_URL = SCOTLAND_API + '/search/product'
SCOTLAND_PORTAL = 'https://remotesensingdata.gov.scot/data'
SCOTLAND_TYPES = {'dtm': 'scotland_lidar_dtm', 'dsm': 'scotland_lidar_dsm', 'laz': 'scotland_lidar_point_cloud'}
RESOLUTION_RE = re.compile(r'^(\d+)(cm|m)$')


class ScottishCollection:
    """What the plugin needs about one catalogue collection."""

    def __init__(self, name, product, year, rights):
        self.name, self.product, self.year, self.rights = name, product, year, rights

    @property
    def non_commercial(self):
        return api.is_non_commercial(self.rights)


def scottish_collection(name, metadata):
    """A ScottishCollection for a catalogue collection name such as 'scotland-gov/lidar/phase-1/dtm', or None
    (e.g. the OGC services collection)."""
    parts = name.split('/')
    if len(parts) < 3 or parts[:2] != ['scotland-gov', 'lidar']:
        return None
    parts = parts[2:]
    kind = next((p for p in parts if p in SCOTLAND_TYPES), None)
    if kind is None:
        return None
    survey = [p for p in parts if p != kind and not RESOLUTION_RE.match(p)]
    if len(survey) > 1 and survey[0] == 'hes':
        survey = survey[1:]  # 'hes/hes-2010' -> 'hes-2010'
    year = _collection_year(metadata, survey)
    tokens = [t for t in '-'.join(survey).lower().split('-') if t]
    # Drop the survey year from the name ('hes-2010' of 2010 -> 'hes'), unless it starts a range ('hes-2016-2017')
    tokens = [t for i, t in enumerate(tokens)
              if t != str(year) or (i + 1 < len(tokens) and tokens[i + 1].isdigit())]
    slug = '-'.join(tokens) or 'survey'
    rights = ' '.join(str((metadata or {}).get('useConstraints') or '').split())[:600]
    if not api.YEAR_RE.match(f'{year:04d}-{slug}'):
        return None  # odd characters in the name: it becomes a folder name
    return ScottishCollection(name, SCOTLAND_TYPES[kind], f'{year:04d}-{slug}', rights)


def _collection_year(metadata, survey):
    extent = (metadata or {}).get('temporalExtent') or {}
    for value in (extent.get('begin'), extent.get('end'), (metadata or {}).get('datasetReferenceDate')):
        match = re.match(r'^(\d{4})', str(value or ''))
        if match:
            return int(match.group(1))
    match = re.search(r'(19|20)\d{2}', '-'.join(survey))
    return int(match.group(0)) if match else 0


def parse_scotland_collections(data):
    """{collection name: ScottishCollection} from the catalogue's collection list."""
    found = {}
    for item in (data or {}).get('result', []):
        collection = scottish_collection(item.get('name', ''), item.get('metadata'))
        if collection is not None:
            found[collection.name] = collection
    return found


def polygon_wkt(polygon):
    """A GeoJSON Polygon (WGS84) as WKT for the catalogue's footprint search."""
    ring = polygon['coordinates'][0]
    return 'POLYGON((' + ', '.join(f'{x} {y}' for x, y in ring) + '))'


def scotland_search_body(collection_names, polygon, limit=SCOTLAND_PAGE, offset=0):
    return {'collections': sorted(collection_names), 'footprint': polygon_wkt(polygon), 'spatialop': 'intersects',
            'offset': offset, 'limit': limit}


def _file_resolution(name):
    """'NS79_1M_DTM_PHASE1.tif' -> '1', 'NS14NE_50CM_DTM_PHASE6.tif' -> '0.5'; '' if not stated."""
    match = re.search(r'_(\d+)(CM|M)_', name.upper())
    if not match:
        return ''
    value = int(match.group(1)) / (100 if match.group(2) == 'CM' else 1)
    return f'{value:g}'


def parse_scotland_products(data, collections):
    """Offerings from a catalogue product search."""
    offerings = []
    for item in (data or {}).get('result', []):
        collection = collections.get(item.get('collectionName'))
        try:
            http = item['data']['product']['http']
            url, size = http['url'], int(http.get('size') or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if collection is None or not api.is_trusted_url(url):
            continue
        name = os.path.basename(url)
        ref = (item.get('properties') or {}).get('osgbGridRef') or name.split('_')[0]
        found = api.square_tiles(ref)
        if not found:
            continue
        folder, tiles = found
        if collection.product == 'scotland_lidar_point_cloud':
            resolution = 'NaN'
        else:
            resolution = _file_resolution(name)
            if not resolution:
                continue
        dataset = api.Dataset(collection.product, collection.year, resolution)
        file_ref = api.FileRef(url, name, folder, size)
        offerings += [api.Offering(dataset, tile, tile, '', (file_ref,), collection.rights) for tile in tiles]
    return merge_offerings(offerings)


# ------------------------------------------------------------------ official portals (and Northern Ireland)

# Where people can find each nation's data themselves (the "Data sources" menu and messages)
OFFICIAL_SOURCES = (
    ('England: Environment Agency survey data', 'https://environment.data.gov.uk/survey'),
    ('Wales: DataMapWales LIDAR', WALES_PORTAL),
    ('Scotland: Scottish Remote Sensing Portal', SCOTLAND_PORTAL),
)
# Northern Ireland isn't downloadable through the plugin yet: its LIDAR is on OpenDataNI as area
# downloads in Irish Grid. These are the official places to find it: elevation data only (LIDAR and
# the DTMs made from it). OpenDataNI's site search is /search?q=...: its old /dataset?... addresses
# answer "500 | Internal Server Error" since the site was rebuilt (checked October 2026).
NI_LINKS = (
    ('LIDAR datasets on OpenDataNI (river basins, Lough Neagh, coast, Belfast)',
     'https://www.opendatani.gov.uk/search?q=lidar'),
    ('OSNI 10 m and 50 m DTMs (OpenDataNI)', 'https://www.opendatani.gov.uk/search?q=OSNI+DTM'),
)
