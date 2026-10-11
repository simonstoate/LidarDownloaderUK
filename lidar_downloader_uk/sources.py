# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Wales and Scotland: where their LIDAR is listed and how it maps onto the plugin's 5 km tiles.

The Environment Agency (England) serves a zip per 5 km tile (api.py). Wales and Scotland serve
files for 1 km, 5 km or 10 km squares, found through their own catalogues:

- Wales, DataMapWales (Welsh Government, Natural Resources Wales): WFS tile catalogues in EPSG:27700.
  The 2020-22 national survey has a 1 m DTM / DSM GeoTIFF per 1 km square; the archive (1998-2015) a
  zip per 10 km square and survey, listed as tiles of 2 km, 1 km or 500 m.
- Scotland, the Scottish Remote Sensing Portal (Scottish Government, JNCC): a catalogue API searched
  by footprint (WKT in WGS84), listing each product's file on the portal's S3 bucket.

Kept free of QGIS/Qt imports so it can be unit tested with plain pytest; tasks.py makes the requests.
"""

import os
import re
from urllib.parse import quote

from . import api, places

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
        surveys = known.surveys + tuple(s for s in offering.surveys if s not in known.surveys)
        merged[key] = api.Offering(known.dataset, known.tile, known.tile_id, known.url, files,
                                   known.rights or offering.rights, surveys)
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


MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _national_survey(props):
    """When a 1 km square of the national survey was flown (the catalogue writes '22-03-2020'), or None."""
    place = places.parse_grid_reference(str(props.get('british_gr') or ''))
    match = re.fullmatch(r'(\d{1,2})-(\d{1,2})-(\d{4})', str(props.get('date') or '').strip())
    if place is None or place.extent is None or not match or not 1 <= int(match.group(2)) <= 12:
        return None
    day, month, year = (int(n) for n in match.groups())
    return api.SurveyArea(tuple(place.extent), year, f'{day} {MONTHS[month - 1]} {year}', 'Welsh Government national',
                          api.year_label(api.Dataset('wales_lidar_dtm', WALES_NATIONAL_YEAR, '1')))


def parse_wales_national(data):
    """Offerings from the 2020-22 national survey catalogue: a DTM and a DSM GeoTIFF per 1 km square (and the date
    it was flown)."""
    offerings = []
    for feature in (data or {}).get('features', []):
        props = feature.get('properties') or {}
        found = api.square_tiles(props.get('british_gr'))
        if not found:
            continue
        folder, tiles = found
        survey = _national_survey(props)
        for product, field in (('wales_lidar_dtm', 'dtm_link'), ('wales_lidar_dsm', 'dsm_link')):
            url = _https(props.get(field))
            if not api.is_trusted_url(url):
                continue
            dataset = api.Dataset(product, WALES_NATIONAL_YEAR, '1')
            ref = api.FileRef(url, os.path.basename(url), folder)
            offerings += [api.Offering(dataset, tile, tile, '', (ref,), surveys=(survey,) if survey else ())
                          for tile in tiles]
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


def outline_bounds(geometry):
    """(xmin, ymin, xmax, ymax) of a catalogue tile's outline (a GeoJSON polygon in British National Grid metres, a
    fraction of a millimetre off the whole metre: rounded to it), or None for no usable outline: none, or one
    bigger than a 10 km square."""
    try:
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        xs, ys = zip(*((float(p[0]), float(p[1])) for polygon in polygons for ring in polygon for p in ring))
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    x0, y0, x1, y1 = round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))
    return (x0, y0, x1, y1) if x1 - x0 <= 10000 and y1 - y0 <= 10000 else None


def name_bounds(name):
    """(xmin, ymin, xmax, ymax) of the square a catalogue tile is named by: a 1 km square ('ST1476') or a quarter of
    one ('ST3281SW'), or None."""
    name = (name or '').strip().upper()
    place = places.parse_grid_reference(name[:6])
    if place is None or place.extent is None or place.extent[2] - place.extent[0] != 1000:
        return None
    x0, y0, x1, y1 = place.extent
    if re.fullmatch(r'[A-Z]{2}\d{4}', name):
        return x0, y0, x1, y1
    if not re.fullmatch(r'[A-Z]{2}\d{4}(NE|NW|SE|SW)', name):
        return None
    x0 += 500 if name[-1] == 'E' else 0
    y0 += 500 if name[-2] == 'N' else 0
    return x0, y0, x0 + 500, y0 + 500


def flown_text(value):
    """The archive catalogue's date flown, as text to read: '1---5-Apr-2009' -> '1 to 5 Apr 2009', 'Oct---Nov-2008'
    -> 'Oct to Nov 2008', '28-March-1998' -> '28 March 1998' (it's free text, not always a day)."""
    text = re.sub(r'-{2,}', ' to ', str(value or '').strip())
    return re.sub(r'\s+', ' ', text.replace('-', ' ')).strip()


def _archive_survey(feature, props):
    """When an archive catalogue tile was flown, and where (its outline, or the square it's named by), or None. The
    survey is named as its zip is, without '_dtm.zip' ('25cm_res_SN14_2007'): the catalogue's own names are the
    tiles'."""
    bounds = outline_bounds(feature.get('geometry')) or name_bounds(props.get('gb_ng'))
    year = re.match(r'\d{4}', str(props.get('year') or '').strip())
    if bounds is None or not year:
        return None
    url = str(props.get('dtm_url') or props.get('dsm_url') or '').strip()
    name = re.sub(r'_(dtm|dsm)\.zip$', '', os.path.basename(url), flags=re.IGNORECASE)
    return api.SurveyArea(bounds, int(year.group()), flown_text(props.get('date_flown')) or year.group(),
                          'NRW archive', name)


def outline_tiles(geometry, square):
    """The 5 km tiles of a 10 km square (e.g. 'ST17') that a catalogue tile's outline (see outline_bounds) covers by
    more than an edge. [] for no usable outline."""
    bounds = outline_bounds(geometry)
    if bounds is None:
        return []
    x0, y0, x1, y1 = bounds
    tiles = []
    for col in range(int(x0 // 5000), int(x1 // 5000) + 1):
        for row in range(int(y0 // 5000), int(y1 // 5000) + 1):
            width = min(x1, (col + 1) * 5000) - max(x0, col * 5000)
            height = min(y1, (row + 1) * 5000) - max(y0, row * 5000)
            tile = places.tile_at(col * 5000 + 2500, row * 5000 + 2500) if width > 0 and height > 0 else None
            if tile and tile.startswith(square):
                tiles.append(tile)
    return tiles


def parse_wales_archive(data):
    """Offerings from the archive catalogue: tiles of 2 km, 1 km or 500 m, each named by the 1 km square at its
    south-west corner ('ST1476'; 'ST3281SW' for a quarter of one) and pointing at a zip for its 10 km square and
    survey. A tile is offered for every 5 km tile its outline covers, as a 2 km tile can cross into the next one;
    a row without an outline goes by its name. Each offering keeps when its tiles were flown."""
    offerings = []
    for feature in (data or {}).get('features', []):
        props = feature.get('properties') or {}
        year, resolution = _archive_year(props.get('year')), _archive_resolution(props.get('resolution'))
        if not year or not resolution:
            continue
        found = api.square_tiles(props.get('gb_ng'))
        survey = _archive_survey(feature, props)
        square = str(props.get('_10k') or '').strip().upper()
        for product, field in (('wales_lidar_archive_dtm', 'dtm_url'), ('wales_lidar_archive_dsm', 'dsm_url')):
            url = _https(props.get(field))
            if not api.is_trusted_url(url):
                continue
            zip_square = square if api.SQUARE_10KM_RE.match(square) else _square_in_name(url)
            if not zip_square:
                continue
            # The zip's files are kept in its 10 km square's folder, so only that square's tiles can use them
            tiles = outline_tiles(feature.get('geometry'), zip_square) or [
                tile for tile in (found[1] if found else []) if tile.startswith(zip_square)]
            dataset = api.Dataset(product, year, resolution)
            ref = api.FileRef(url, os.path.basename(url), zip_square)
            offerings += [api.Offering(dataset, tile, tile, '', (ref,), surveys=(survey,) if survey else ())
                          for tile in tiles]
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
DENSITY_RE = re.compile(r'^\d+ppm$')  # point cloud collections by density: 'outerheb-2019/laz/4ppm'
# Phase 2's point clouds come as files of whole 5 km tiles: about 8 MB for each km² that has data (measured against
# their DTMs, October 2026), which tells how much of a tile has data
CLOUD_MB_PER_KM2 = 8
# Phase 1 and 2's DTMs and DSMs of 10 km squares (1 m) are cut to their data: about 1.5 MB per km² of it (their
# biggest files, October 2026), which tells how much of the square one covers where nothing smaller says where
GRID_10KM_MB_PER_KM2 = 1.5


class ScottishCollection:
    """What the plugin needs about one catalogue collection. survey names the survey its DTM, DSM and point
    cloud collections share ('phase-1', 'hes/hes-2010')."""

    def __init__(self, name, product, year, rights, survey=''):
        self.name, self.product, self.year, self.rights, self.survey = name, product, year, rights, survey

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
    shared = '/'.join(p for p in parts if p not in SCOTLAND_TYPES and not RESOLUTION_RE.match(p)
                      and not DENSITY_RE.match(p))
    return ScottishCollection(name, SCOTLAND_TYPES[kind], f'{year:04d}-{slug}', rights, shared)


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


def _scottish_files(data, collections):
    """(collection, url, size, grid reference, its square (xmin, ymin, xmax, ymax), share of it with data) per
    product in a search: the share is estimated from the size of a point cloud of a 5 or 10 km square, and of a
    DTM or DSM of a 10 km square."""
    for item in (data or {}).get('result', []):
        collection = collections.get(item.get('collectionName'))
        try:
            http = item['data']['product']['http']
            url, size = http['url'], int(http.get('size') or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if collection is None or not api.is_trusted_url(url):
            continue
        ref = (item.get('properties') or {}).get('osgbGridRef') or os.path.basename(url).split('_')[0]
        place = places.parse_grid_reference(ref)
        if place is None or place.extent is None or not api.square_tiles(ref):
            continue
        square = tuple(place.extent)
        km2 = (square[2] - square[0]) * (square[3] - square[1]) / 1e6
        share = 1.0
        if collection.product == 'scotland_lidar_point_cloud' and km2 >= 25 and size:
            share = min(1.0, size / 1e6 / CLOUD_MB_PER_KM2 / km2)
        elif km2 >= 100 and size:
            share = min(1.0, size / 1e6 / GRID_10KM_MB_PER_KM2 / km2)
        yield collection, url, size, ref, square, share


def _inside(inner, outer):
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def parse_scotland_products(data, collections):
    """Offerings from a catalogue product search, each with where its file lies (api.SurveyArea).

    A file of a 5 or 10 km square (Phase 1 to 6's DTMs and DSMs) is given its whole square by the catalogue, even
    where it covers only a corner of it (Phase 1 and 2's are cut to their data). Where the same survey has smaller
    files in that square (its point clouds: 1 km squares, or Phase 2's 5 km tiles), they show where it has data:
    the file is offered for the tiles they reach, and covers what they cover (for Phase 2's, as much as their size
    says). Search whole 10 km squares so a survey's files in one come together (grid.search_areas)."""
    files = list(_scottish_files(data, collections))
    smaller = {}  # {survey: {square of one of its files: share of it with data}}
    for collection, _, _, _, square, share in files:
        known = smaller.setdefault(collection.survey, {})
        known[square] = max(share, known.get(square, 0.0))
    offerings = []
    for collection, url, size, ref, square, share in files:
        name = os.path.basename(url)
        folder, tiles = api.square_tiles(ref)
        parts = [(square, share)]
        if square[2] - square[0] >= 5000:
            inner = sorted((s, k) for s, k in smaller.get(collection.survey, {}).items()
                           if s != square and _inside(s, square) and s[2] - s[0] < square[2] - square[0])
            if inner:
                parts = inner
                tiles = sorted({places.tile_at((s[0] + s[2]) / 2, (s[1] + s[3]) / 2) for s, _ in inner})
        if collection.product == 'scotland_lidar_point_cloud':
            resolution = 'NaN'
        else:
            resolution = _file_resolution(name)
            if not resolution:
                continue
        dataset = api.Dataset(collection.product, collection.year, resolution)
        file_ref = api.FileRef(url, name, folder, size)
        areas = tuple(api.SurveyArea(part, api.year_number(collection.year), '', 'scotland', collection.survey, k)
                      for part, k in parts)
        offerings += [api.Offering(dataset, tile, tile, '', (file_ref,), collection.rights, areas) for tile in tiles]
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
