# Roadmap

## v0.2: works on QGIS 3 and QGIS 4 ✅

- [x] Qt5/Qt6 compatibility (`qgis.PyQt` only, scoped enums, `exec()`, no `resources.py`)
- [x] `metadata.txt`: `supportsQt6=True`, `qgisMinimumVersion=3.22`, fixed malformed lines, real URLs
- [x] Fix: downloaded zips not recognised (tile names are 6 characters, e.g. `SU12NW`)
- [x] Fix: crash when the grid layer is removed manually; checkbox now stays in sync
- [x] Fix: `layer.setVisible()` doesn't exist (errored when showing the grid again)
- [x] Fix: `unload()` now removes the grid layer, disconnects signals and deletes the dock
- [x] Fix: progress bar advances for failed tiles
- [x] Failed or corrupt downloads are no longer treated as "already downloaded"; request timeouts added
- [x] Headless smoke test (`tests/smoke_test_qgis.py`)
- [x] Git repo, README, LICENSE, `.gitignore`

## v0.2.1: restore downloads ✅

- [x] `api.agrimetrics.co.uk` was retired (no DNS records); switched to `https://environment.data.gov.uk/tiles/collections/survey/{product}/{year}/{res}/{tileId}`, the endpoint behind the survey portal's download links (no subscription key)
- [x] Endpoint and tile maths in `api.py`, with plain-Python unit tests
- [x] Stream downloads to disk; per-tile failure reasons (e.g. "no data available" outside England)

Notes on the service (checked September 2026):
- Only `lidar_composite_dtm` **2022** exists, at resolution `1` or `2` m; other years return HTTP 500.
- Tiles with no data return HTTP **500** (not 404).
- The portal also calls `POST /backend/catalog/api/tiles/collections/survey/search` (GeoJSON in, available products/tiles out). It rejected direct requests with 403 when tested, so it needs more investigation before v0.5's product and year discovery can use it.

## v0.3: reliability and responsiveness ✅

- [x] Downloads run in a background `QgsTask` using `QgsBlockingNetworkRequest`: no UI freeze, respects QGIS proxy/auth settings, Cancel button, progress in MB and in the QGIS task bar
- [x] Retries for timeouts, connection errors and 502/503/504; no retry for "no data" (500)
- [x] Validate zips (corrupt ones are deleted); `.part` then rename
- [x] One question up front for already-downloaded tiles (replaces per-tile Yes/No/Yes to All)
- [x] Code split into `api.py`, `storage.py`, `tasks.py`, `grid.py` and the plugin/UI module
- [x] Boilerplate tests replaced: pytest unit tests plus a headless QGIS smoke test (faked network; `LIDAR_LIVE=1` for a real download)
- [x] GitHub Actions: flake8 + pytest, and the smoke test in `qgis/qgis` 3.40, 3.44 (Qt5) and 4.2 (Qt6)
- [x] Release workflow: pushing a `vX.Y` tag builds the plugin zip and attaches it to a GitHub release (simple `zip`; revisit `qgis-plugin-ci` when publishing to plugins.qgis.org)
- [x] Removed Plugin Builder leftovers (`help/`, `i18n/`, `scripts/`, `plugin_upload.py`, `pylintrc`, `pb_tool.cfg`)

Known limitation: each tile is held in memory (~70 MB) while it downloads, because `QgsBlockingNetworkRequest` buffers the reply. That's fine for 5 km tiles; revisit if larger products (e.g. point clouds) are added.

## v0.4: UX ✅

- [x] Dock rebuilt with Qt layouts (resizes cleanly, fits a narrow side panel), grouped as 1. Choose / 2. Download / 3. Use
- [x] Selection helpers: "Tiles in map view" and "Tiles under layer" (selected features, all features or raster extent; any CRS)
- [x] Tiles outside England styled grey and skipped on download; selection label shows the count and size estimate
- [x] Message bar and Log Messages panel instead of modal pop-ups; one combined "already downloaded / large download" question
- [x] Download folder saved on change ("Set as default" checkbox removed)
- [x] Grid only removed when the dock is closed, not when it's tabbed behind another dock
- [x] Grid shipped as a GeoPackage (single file, spatial index, only the needed columns)
- [x] CI also covers QGIS 3.22 (the declared minimum) and 3.34

## v0.4.1 ✅

- [x] Option to delete zip files after a successful extraction (saved setting). Tiles with only extracted rasters count as downloaded, and empty extracted folders don't.

## v0.4.2 ✅

- [x] "Tidy up" button: delete existing zips whose tiles are fully extracted (every zip member present at the same size), with a confirmation showing the space freed

## v0.4.4 ✅

- [x] Grid labelled with tile names (scale-limited to 1:500,000 and closer)
- [x] "Open" button for the download folder
- [x] Zip clean-up renamed "Delete extracted zips...": explicit warning (folder, files, sizes, what's kept), Cancel by default, files go to the Recycle Bin/Trash where Qt supports it

## v0.4.5 ✅

- [x] Zip clean-up deletes permanently (no Recycle Bin): the warning states it plainly and notes tiles can be re-downloaded

## v0.4.6 ✅

- [x] New icon (SVG for the toolbar, 128 px PNG for the Plugin Manager)

## v0.5.0: choose what to download ✅

- [x] Discovered the Defra search API: `POST https://environment.data.gov.uk/tiles/collections/survey/search` with a GeoJSON **Polygon** (WGS84) returns every product/year/resolution per tile, with download URLs. MultiPolygon/Feature bodies are rejected; responses are fast and unpaged.
- [x] Dataset list in the dock, filled from the search API for the selected tiles (debounced, cached per tile, one request per 100 km square), with tile counts; Composite 2022 datasets always listed
- [x] "Latest available" pseudo-dataset for multi-year products (National LIDAR Programme years vary by area, 2018-2023)
- [x] Per-dataset folders; the default Composite DTM 2022 1m keeps the main folder (backwards compatible)
- [x] Point clouds (.laz) load as `QgsPointCloudLayer`; loaded tiles grouped by dataset; VRT limited to rasters
- [x] Per-dataset size estimates (measured Sept 2026); downloads written from Qt's buffer (no extra copy)
- [x] Background tasks catch unexpected errors instead of crashing QGIS

## v0.5.1: aerial photography ✅

- [x] Vertical aerial photography (RGB, RGBN, IRRGB, night-time) from the same search API: ECW orthophotos loaded as rasters, black borders transparent, VRT supported
- [x] Oblique incident-response photos: GPS-tagged JPEGs turned into direction arrows with photo map tips (native:importphotos)
- [x] Imagery listed after the LiDAR datasets, behind a separator

## v0.5.x: next

- [x] v0.5.2: Processing algorithm "Download LIDAR / aerial tiles" (layer and/or extent, product, year/latest, resolution/finest, shared folders, optional VRT loaded on completion; max 300 tiles)
- [x] v0.5.3: Hillshade and Clip to layer (dock and Processing outputs), Cloud-Optimised GeoTIFF via GDAL, "Load tiles after downloading", scrolling dock
- [x] Intermittent segfaults on QGIS 3.x (Qt5), traced with gdb in CI (v0.6.1): the dataset menu was rebuilt inside its own action's signal, download progress used lambdas connected in worker threads (PyQt proxies destroyed when pool threads exit), and the smoke test held wrappers of deleted style rules. All fixed; 16/16 runs green on 3.22-3.44.
- [ ] Publish to plugins.qgis.org (needs an OSGeo account; or a custom plugins.xml repository on GitHub first)

## v0.5.4: feedback round ✅

- [x] Dataset menu grouped by product + resolution, survey years (and "latest available") as sub-menus
- [x] v0.5.5: only products at the top level; resolutions and years inside (a resolution with several years gets its own sub-menu)
- [x] No duplicate layers: tiles, VRTs, hillshades/clips and photo points already loaded are skipped (or refreshed)
- [x] Grid moved to the top of the Layers panel whenever "Show OSGB 5 km grid" is ticked; dataset groups go just below it
- [x] "Load tiles after downloading" at the bottom of the Download section
- Decided: no streaming of large downloads to disk (buffering in memory is fine for this plugin)

## v0.6.0: products, shading by survey, folder layout ✅

- [x] Choose a whole product ("all years and resolutions"): newest, then finest, survey per tile
- [x] Grid shaded per survey (year / resolution) with a legend in the dock (and in the Layers panel); v0.6.1: legend in its own section at the bottom of the panel
- [x] Folder layout follows the Dataset menu: <product>/<resolution>/<year>/<tile>/; "latest" downloads go into each tile's real survey folder
- [x] Migration of earlier layouts after asking: rename-only (a failed move changes nothing), open layers detached and re-pointed (Windows locks open files), VRTs rewritten (relative, absolute and mixed-slash paths), survey year of old "latest" folders read from file-name dates (YYYYMMDD or _YYMMDD_). Rehearsed on a replica of a real download folder.
- [x] Download folder filled in when QGIS starts (it previously only appeared after clicking the plugin icon), and never falls back to a folder inside the plugin

## v0.6.2: styles ✅

- [x] Built-in styles applied on load: Hillshade (z 5, multidirectional, multiply), Contours (1 m / 5 m index), Elevation colours
- [x] User styles: save the active layer's style, or add a .qml; stored in the QGIS profile; open the folder from the menu
- [x] Several styles: a styled copy each in sub-groups, stacked in menu order; apply to the active layer; no duplicate copies
- [x] Replaces the dock's "Hillshade..." GeoTIFF button (the Processing tool keeps its optional hillshade output)

## v0.6.3: styles on the group ✅

- [x] Styles applied to a mosaic (VRT) of all the tiles loaded into a dataset's group: colour stretch over the group's min/max, seamless hillshade and contours; the mosaic and its styled layers update as tiles are added
- [x] Contours as generated lines (GDAL, in the background) with the 5 m index contours labelled with their elevation
- [ ] Maybe: contour smoothing / a coarser option for flat ground (1 m contours on 1 m LiDAR are noisy on floodplains)

## Next (planned after a user review, Sept 2026)

The plugin stays general-purpose: features are named and designed for anyone using this data, with
options covering specialist needs (e.g. flood modelling and CAD formats).

### v0.7: easier for everyone

- [x] "Pick on map" tool (click / drag, whatever layer is active) and "Draw area" (rectangle / polygon, optional buffer) *(0.7.0)*
- [x] Coverage preview: shade the tiles that have the chosen dataset before downloading *(0.7.0)*
- [x] *(0.7.1)* Search: place name, postcode or OS grid reference
- [x] *(0.7.1)* Contour interval (0.5 / 1 / 2 / 5 m, index every 5th); cancel from the task bar
- [x] *(0.8.2)* Contour smoothing (1 m contours on 1 m LiDAR are noisy on flat floodplains) and a size warning
- [x] *(0.7.0)* Survey dates layer from the Composite DTM metadata (`lidar_used_in_merging_process`: SD_FLOWN / ED_FLOWN / SRVY_YEAR)
- [x] *(0.7.1)* DTMs set up as elevation surfaces for the Elevation Profile tool
- [x] *(0.8.2)* ... and as the project's terrain for the 3D view, on request ("Use as 3D terrain"; asks before replacing one)
- [x] *(0.7.1)* OGL attribution written into layer metadata automatically
- [x] *(0.7.1)* Dataset descriptions in the dock (what a first-return DSM / VOM / intensity is); offer to switch the project to EPSG:27700

### v0.8: terrain outputs

- [x] *(0.8.0)* Export terrain: clip to a polygon (+ buffer), resample to a cell size, GeoTIFF / ESRI ASCII / FLT / XYZ, consistent no-data value (tiles use -3.4e38)
- [x] *(0.8.0)* Best-available mosaic: fill gaps in the newest survey from older ones (exports)
- [x] *(0.8.0)* Contour export: GeoPackage / shapefile / 3D DXF with elevations
- [x] *(0.8.1)* Change between surveys (DEM of difference) and DSM - DTM (building / vegetation height), with volumes
- [x] *(0.8.0)* The same outputs as Processing algorithms (models, batch): Export terrain, Export contours

### v0.9: point clouds, housekeeping, publishing

- [x] *(0.9.0)* Virtual point cloud across tiles (pdal:virtualpointcloud), classification style, clip to area
- [x] *(0.9.0)* DTM / DSM made from the points at any cell size (ground points / first returns, gaps filled)
- [x] *(0.9.2)* "My downloads" panel: space per dataset; show / load / open / delete
- [x] *(0.9.2)* 3 parallel downloads (QGIS subtasks); free disk space check before downloading
- [x] *(0.9.3)* Help button; screenshots and getting started in the README
- [ ] Publish to plugins.qgis.org
- [x] *(0.9.3)* Small tidy-ups: QGIS 4 deprecation warning in the colour stretch; styles / contour options in the Processing tool

## v0.9.5: the full OS grid ✅

- [x] Every square of the OS 5 km grid (osbng-grids), with an "Environment Agency has data" flag built from the service (`tools/build_grid.py`), instead of land squares by country: coastal / estuary tiles with data were missing or blocked
- [x] Coastal multibeam bathymetry, IRRGBN photography, oblique photos from other surveys

## v0.10: Wales, Scotland, Northern Ireland links ✅

- [x] Wales from DataMapWales (WFS tile catalogues): 2020-22 national survey DTM/DSM, NRW archive 1998-2015
- [x] Scotland from the Scottish Remote Sensing Portal (catalogue API): every open LiDAR collection, by survey
- [x] 10 km files shared by their four 5 km tiles; per-survey licences, with a prompt for non-commercial ones
- [x] Northern Ireland: Irish Grid references, NI postcodes / OSNI places, a notice and "Official data sources" links (as FloodMaps UK)
- [x] Grid `HAS_DATA` flag from all three sources

## v0.11: LIDAR Downloader UK ✅

- [x] Renamed (plugin folder `lidar_downloader_uk`, repository LidarDownloaderUK); settings keys and Processing IDs kept
- [x] Auditor review fixes: partial search failures, paging, per-tile completion markers, Processing licence
  terms and survey years, redirect and folder-name checks, zip size check, wording

## v0.12: one job, done well ✅

The plugin finds, downloads, organises and loads the data; analysis moves to a companion plugin.

- [x] Crop to a layer (drawn area or site boundary) when loading and saving mosaics; download and crop in one go
- [x] One seamless mosaic per dataset on loading, coloured by height; "Create mosaic..." saves a VRT or GeoTIFF copy
- [x] Clear removes the drawn area; no buffer
- [x] Removed (to the companion plugin): styles (hillshade, contours, user styles), terrain / contour exports,
  difference, surfaces from point clouds, 3D terrain, river-bed burn-in. The last code with them is tagged
  `analysis-tools-archive`.

## v0.13: correct data, clean security scan ✅

A full review before publishing.

- [x] Welsh archive (NRW) heights converted from millimetres to metres (they loaded 1000x too high)
- [x] Mosaics of mixed surveys: the newest on top, at the finest cell size
- [x] Safer cropping: trimmed to the tiles, self-crossing polygons repaired, never stops loading
- [x] A service counts as down only after two files get no answer; plain "isn't responding" messages
- [x] Bandit and detect-secrets clean (plugins.qgis.org scans every upload)

## v0.14: easier from the first click ✅

- [x] Panel laid out as the steps: 1. Choose your area, 2. Choose the data, 3. Download, 4. Use downloaded
  tiles, then the download folder
- [x] Grid shown when the panel opens; Documents/LiDAR Data made without asking
- [x] Datasets named by nation and product, year before resolution, "newest for each tile"; a product with one
  survey chosen directly; nation submenus across a border; non-commercial surveys marked
- [x] Switch to the nation's own DTM / DSM / point cloud when the chosen dataset covers none of the tiles
- [x] Size estimates from real or typical file sizes (10 km files counted once); disk check allows for extracting
- [x] Resume unfinished downloads
- [x] Overviews on downloaded grids (big mosaics draw quickly zoomed out)
- [x] My downloads: where each survey is (10 km squares)
- [x] Processing: products in the menu's order, the panel's folder by default, "1 m" / "50cm", VRTs named
  after their tiles

## v0.14.1: tidy-up for publishing ✅

A review of the code as an open-source project, and of what plugins.qgis.org checks.

- [x] Licence (SPDX) line and a purpose docstring in every file; Plugin Builder boilerplate and the unused
  translation loader removed; stale names and comments brought up to date
- [x] Longest functions split (saving a raster mosaic; the Processing tool's availability check); 120-character lines
- [x] The site's scans pass locally: Bandit and detect-secrets clean, flake8 clean with the bundled `.flake8`
- [x] Full history moved to CHANGELOG.md; metadata keeps the recent entries

## Publishing

- [x] v0.14.3: the first public release. Its metadata changelog starts afresh (one entry; the development
  history is in CHANGELOG.md), and the shading of tiles that have the chosen data works with up to 1,600
  tiles in view (was 400), with a note under its tick box saying what it's doing
- [x] v0.14.4: passes the plugin site's Qt6 compatibility check (enums named in full in the fallbacks for
  older QGIS 3 versions)
- [x] v0.14.5: metadata follows the plugin site's QGIS 4 rules (`qgisMaximumVersion=4.99`, no `supportsQt6`)
- [x] Make the repository public (Issues on), then submit to plugins.qgis.org (as experimental first)
- [ ] Approved on plugins.qgis.org; check it installs from the Plugin Manager (with experimental plugins shown)

## Companion plugin (planned)

Styling, terrain and contour exports (DXF for CAD), river-bed burn-in, differences between surveys, surfaces from
point clouds, and catchment / sub-catchment generation, working on any elevation data. Seeded from the
`analysis-tools-archive` tag.

## Later

- [ ] Best available DTM / DSM: one choice that gives each tile the best elevation data from any product or
  survey (deferred from v0.14)
- [ ] Split the main module (lidar_downloader.py, about 2,200 lines) by panel step, e.g. the downloads and the
  loading / mosaic code in modules of their own
- [ ] Northern Ireland downloads, if OpenDataNI's LiDAR becomes tiled / easier to reach (list the datasets in view first)
- [ ] Survey dates for Wales (the WFS has flown dates per 1 km square)
- [x] *(0.9.1)* Riverine multibeam bathymetry, burnt into DTM exports on request
- [x] *(0.9.2)* SurfZone DEM 2019 (coastal: land and sea bed)
- [ ] Other products the search API lists: CASI multispectral imagery
