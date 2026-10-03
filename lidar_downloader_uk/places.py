# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Finding places: OS and Irish Grid references (worked out locally), postcodes and place names
(postcodes.io, plus OSNI's gazetteer for Northern Ireland, bundled).

No QGIS imports, so it can be unit tested on its own. Coordinates are British National Grid
(EPSG:27700) metres unless a result only has a longitude / latitude, or names its crs (Irish Grid).
"""

import json
import os
import re
import string
from dataclasses import dataclass
from urllib.parse import quote

POSTCODE_URL = 'https://api.postcodes.io/postcodes/{}'
# OS Open Names: cities, towns, villages, hamlets, suburbs...
PLACES_URL = 'https://api.postcodes.io/places?q={}&limit={}'
MAX_PLACES = 10

GRID_LETTERS = string.ascii_uppercase.replace('I', '')  # the grid's letters skip I
LETTER_REF_RE = re.compile(r'^([A-HJ-Z]{2})(\d*)(NE|NW|SE|SW)?$')
NUMERIC_REF_RE = re.compile(r'^(\d{1,6}(?:\.\d+)?)\s*[,\s]\s*(\d{1,7}(?:\.\d+)?)$')
POSTCODE_RE = re.compile(r'^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$')
# The extent of the OS national grid
MAX_EASTING, MAX_NORTHING = 700000, 1300000


@dataclass(frozen=True)
class Place:
    """Somewhere to go: a label, a point and, if known, an area (xmin, ymin, xmax, ymax).

    easting / northing are None when only lon / lat are known (e.g. a terminated postcode).
    """
    label: str
    easting: float = None
    northing: float = None
    extent: tuple = None
    lon: float = None
    lat: float = None
    # The coordinate system of easting / northing / extent when it isn't British National Grid
    crs: str = ''


def square_origin(letters):
    """South-west corner (metres) of a 100 km square such as 'SU', or None if it's off the grid."""
    letters = letters.upper()
    if len(letters) != 2 or any(c not in GRID_LETTERS for c in letters):
        return None
    l1, l2 = GRID_LETTERS.index(letters[0]), GRID_LETTERS.index(letters[1])
    east = ((l1 - 2) % 5) * 5 + l2 % 5
    north = (19 - (l1 // 5) * 5) - l2 // 5
    e, n = east * 100000, north * 100000
    if not (0 <= e < MAX_EASTING and 0 <= n < MAX_NORTHING):
        return None
    return e, n


def parse_grid_reference(text):
    """A Place for an OS grid reference, or None if the text isn't one.

    Accepts 'SU', 'SU12', 'SU1427', 'SU 14 27', 'SU 14567 27890', a 5 km tile such as 'SU12NE',
    and 'easting, northing' in metres (e.g. '414360, 130135'). Letter references cover a square
    (the extent); a numeric one is a point.
    """
    compact = re.sub(r'\s+', '', text or '').upper()
    match = LETTER_REF_RE.match(compact)
    if match:
        letters, digits, quadrant = match.groups()
        origin = square_origin(letters)
        if origin is None or len(digits) % 2 or len(digits) > 10:
            return None
        e, n = origin
        if quadrant:
            if len(digits) != 2:
                return None
            e += int(digits[0]) * 10000 + (5000 if quadrant[1] == 'E' else 0)
            n += int(digits[1]) * 10000 + (5000 if quadrant[0] == 'N' else 0)
            size = 5000
        else:
            half = len(digits) // 2
            size = 10 ** (5 - half)
            if half:
                e += int(digits[:half]) * size
                n += int(digits[half:]) * size
        return Place(compact, e + size / 2, n + size / 2, (e, n, e + size, n + size))
    match = NUMERIC_REF_RE.match((text or '').strip())
    if match:
        e, n = float(match.group(1)), float(match.group(2))
        if 0 <= e < MAX_EASTING and 0 <= n < MAX_NORTHING:
            return Place(f'{e:.0f}, {n:.0f}', e, n)
    return None


def tile_at(easting, northing):
    """The 5 km tile (e.g. 'SU12NE') a point in British National Grid metres is in, or None if it's off the grid."""
    if not (0 <= easting < MAX_EASTING and 0 <= northing < MAX_NORTHING):
        return None
    e100, n100 = int(easting // 100000), int(northing // 100000)
    first = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
    second = (19 - n100) * 5 % 25 + e100 % 5
    e, n = easting % 100000, northing % 100000
    return (f'{GRID_LETTERS[first]}{GRID_LETTERS[second]}{int(e // 10000)}{int(n // 10000)}'
            f'{"N" if n % 10000 >= 5000 else "S"}{"E" if e % 10000 >= 5000 else "W"}')


def tile_cell(tile):
    """(column, row) of a 5 km tile among the grid's 5 km squares (its south-west corner / 5000), or None if it isn't
    a tile name."""
    place = parse_grid_reference(tile)
    if place is None or place.extent is None or place.extent[2] - place.extent[0] != 5000:
        return None
    return int(place.extent[0] // 5000), int(place.extent[1] // 5000)


def fills_rectangle(tiles):
    """True if there are two or more tiles and together they fill a square or rectangle of the grid, with no gaps,
    so a mosaic of them has no empty space."""
    names = {tile.upper() for tile in tiles}
    cells = {tile_cell(name) for name in names}
    if len(names) < 2 or None in cells:
        return False
    cols, rows = [col for col, _ in cells], [row for _, row in cells]
    return len(cells) == (max(cols) - min(cols) + 1) * (max(rows) - min(rows) + 1)


def tiles_ref(tiles):
    """A short name for some tiles, for file and layer names: the tile itself ('SU12NE'); its 10 km square for all
    four of its tiles ('SU12'); two or three tiles by name ('SU12NE_SU12NW'); more by the tiles at the south-west and
    north-east corners of the area they cover ('SU12SW-SU22NE')."""
    names = sorted({tile.upper() for tile in tiles})
    if len(names) == 1:
        return names[0]
    squares = {name[:-2] for name in names}
    if len(names) == 4 and len(squares) == 1 and all(tile_cell(name) for name in names):
        return squares.pop()
    cells = [cell for cell in (tile_cell(name) for name in names) if cell]
    if len(names) <= 3 or len(cells) < len(names):
        return '_'.join(names)
    cols, rows = [col for col, _ in cells], [row for _, row in cells]
    return f'{tile_at(min(cols) * 5000, min(rows) * 5000)}-{tile_at(max(cols) * 5000, max(rows) * 5000)}'


def is_postcode(text):
    return bool(POSTCODE_RE.match((text or '').strip().upper()))


def lookup_url(text):
    """The postcodes.io URL to look the text up with (a postcode, otherwise a place name)."""
    text = (text or '').strip()
    if is_postcode(text):
        return POSTCODE_URL.format(quote(text.upper()))
    return PLACES_URL.format(quote(text), MAX_PLACES)


def parse_postcode_response(data):
    """Places from a postcodes.io /postcodes response (including terminated postcodes)."""
    result = data.get('result') if isinstance(data, dict) else None
    if isinstance(result, dict):
        area = ', '.join(p for p in (result.get('admin_district'), result.get('country')) if p)
        label = result.get('postcode', '') + (f' ({area})' if area else '')
        # BT postcodes' eastings / northings are Irish Grid, not British National Grid: use lon / lat there
        in_ni = result.get('country') == 'Northern Ireland' or str(result.get('postcode', '')).upper().startswith('BT')
        if not in_ni and result.get('eastings') is not None and result.get('northings') is not None:
            return [Place(label, float(result['eastings']), float(result['northings']))]
        if result.get('longitude') is not None:
            return [Place(label, lon=float(result['longitude']), lat=float(result['latitude']))]
        return []
    terminated = data.get('terminated') if isinstance(data, dict) else None
    if isinstance(terminated, dict) and terminated.get('longitude') is not None:
        return [Place(f"{terminated.get('postcode', '')} (no longer in use)",
                      lon=float(terminated['longitude']), lat=float(terminated['latitude']))]
    return []


SETTLEMENT_RANK = {'City': 0, 'Town': 1, 'Village': 2, 'Suburban Area': 3, 'Hamlet': 4, 'Other Settlement': 5}


def parse_places_response(data):
    """Places from a postcodes.io /places response, cities and towns before villages and hamlets of the
    same name (keeping postcodes.io's order otherwise)."""
    places = []
    for item in (data.get('result') or []) if isinstance(data, dict) else []:
        if item.get('eastings') is None or item.get('northings') is None:
            continue
        where = item.get('county_unitary') or item.get('district_borough') or item.get('region') or item.get('country')
        detail = ', '.join(p for p in (item.get('local_type'), where) if p)
        label = (item.get('name_1') or '?') + (f' ({detail})' if detail else '')
        extent = None
        if all(item.get(k) is not None for k in ('min_eastings', 'min_northings', 'max_eastings', 'max_northings')):
            extent = (float(item['min_eastings']), float(item['min_northings']),
                      float(item['max_eastings']), float(item['max_northings']))
        places.append((SETTLEMENT_RANK.get(item.get('local_type'), len(SETTLEMENT_RANK)),
                       Place(label, float(item['eastings']), float(item['northings']), extent)))
    return [place for _, place in sorted(places, key=lambda p: p[0])]  # stable: keeps the API's ranking


def parse_lookup_response(text, data):
    return parse_postcode_response(data) if is_postcode(text) else parse_places_response(data)


# ------------------------------------------------------------------ Northern Ireland

# Irish Grid: 100 km squares with one letter (A top left, V bottom left, no I), in EPSG:29902
IRISH_GRID_CRS = 'EPSG:29902'
IRISH_REF_RE = re.compile(r'^([A-HJ-Z])(\d{2,10})$')


def parse_irish_grid_reference(text):
    """A Place (in Irish Grid) for a reference such as 'J 341 743' (Belfast), or None."""
    compact = re.sub(r'\s+', '', text or '').upper()
    match = IRISH_REF_RE.match(compact)
    if not match or len(match.group(2)) % 2:
        return None
    index = GRID_LETTERS.index(match.group(1))
    east, north = (index % 5) * 100000, (4 - index // 5) * 100000
    digits = match.group(2)
    half = len(digits) // 2
    size = 10 ** (5 - half)
    e, n = east + int(digits[:half]) * size, north + int(digits[half:]) * size
    return Place(f'{match.group(1)} {digits[:half]} {digits[half:]} (Irish Grid)', e + size / 2, n + size / 2,
                 (e, n, e + size, n + size), crs=IRISH_GRID_CRS)


def is_in_ireland(lat, lon):
    """True for points on the island of Ireland (without a network lookup). A box west of 5.4 W, with the
    corner north of 55.1 N and east of 5.95 W cut out so the Mull of Kintyre (Scotland) isn't included.
    (FloodMaps UK uses the same test.)"""
    if lon > -5.4 or lat < 51.3 or lat > 55.45:
        return False
    if lat < 52.0 and lon > -6.0:
        return False  # Grassholm and the Smalls (Wales); Ireland's nearest point is Carnsore Point, 6.36 W
    return not (lat > 55.1 and lon > -5.95)


NI_PLACES_FILE = os.path.join(os.path.dirname(__file__), 'data', 'ni_place_names.json')
_ni_places = None


def ni_places():
    """OSNI's gazetteer of 336 Northern Ireland towns and villages (bundled; OGL)."""
    global _ni_places
    if _ni_places is None:
        try:
            with open(NI_PLACES_FILE, encoding='utf-8') as f:
                _ni_places = json.load(f)
        except (OSError, ValueError):
            _ni_places = []
    return _ni_places


def _normalise(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def match_ni_places(query, places=None, limit=5, exact_only=False):
    """Northern Ireland places matching a query: exact names first, then (from 4 characters, unless
    exact_only) names starting with it. Ignores case, spaces and punctuation ('helens bay' finds
    Helen's Bay)."""
    q = _normalise(query)
    if not q:
        return []
    places = ni_places() if places is None else places

    def names(p):
        return [_normalise(n) for n in [p.get('name', '')] + list(p.get('aliases') or [])]
    exact = [p for p in places if q in names(p)]
    prefix = [] if exact_only else [p for p in places if p not in exact and len(q) >= 4
                                    and any(n.startswith(q) for n in names(p))]
    return [Place(f"{p['name']} ({p.get('council', '')}, Northern Ireland)".replace(' (, ', ' ('), lon=p['lng'],
                  lat=p['lat']) for p in (exact + prefix)[:limit]]
