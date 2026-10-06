<img src="lidar_downloader_uk/icon.svg" width="64" align="right" alt="">

# LIDAR Downloader UK (QGIS plugin)

> [!NOTE]
> **This is an independent plugin, not an official one.** It isn't made by, affiliated with or endorsed by the Environment Agency, Defra, the Welsh Government, Natural Resources Wales, the Scottish Government, Ordnance Survey, OSNI or any other organisation whose data it uses. It downloads their open data from their own official services.

Download LIDAR data for **England, Wales and Scotland** straight into QGIS:

- **England** (Environment Agency): DTMs, DSMs, vegetation height, intensity and point clouds, from the 2022 Composite products and the National LIDAR Programme back to single surveys from 1998, plus river bathymetry, the coastal SurfZone DEM and, where flown, aerial photography.
- **Wales** (DataMapWales, Welsh Government / Natural Resources Wales): the 2020-22 national survey DTM/DSM at 1 m, and the NRW archive of surveys from 1998 to 2015.
- **Scotland** (Scottish Remote Sensing Portal, Scottish Government): the Phase 1-6 national LIDAR DTMs/DSMs and point clouds, and the other surveys the portal publishes (Outer Hebrides, Orkney, the national LIDAR programme and Historic Environment Scotland's surveys).
- **Northern Ireland**: not downloadable through the plugin yet. Its LIDAR is published as area downloads in Irish Grid, so the plugin points you to the official sources instead (**Official data sources** menu, and a notice when you go to or view Northern Ireland).

Choose your area on the map (go to a place, postcode or grid reference, pick 5 km OSGB grid tiles, or choose your site's boundary layer), choose the data, and the plugin finds, downloads, organises and loads it: a layer per 5 km tile, reading the files exactly as downloaded, or with **Crop to** ticked, one layer of your site cut from them, so a single click can download and crop in one go. It keeps your downloads in order (one folder per survey, with what's on disk and how much space it takes) and shows when each area was actually flown. Data comes straight from each government's official service (see [Data sources and licences](#data-sources-and-licences)), mostly under the Open Government Licence.

The plugin does one thing, getting the data, and aims to do it well. What you do with the data next (styling, terrain and contour exports, catchments) is for QGIS's own tools, or a companion plugin in the making.

Works on **QGIS 3.22+ (Qt5)** and **QGIS 4.x (Qt6)**.

## Getting started

<img src="docs/dock.png" width="300" align="right" alt="The LIDAR Downloader UK panel: 1. Choose your area, 2. Choose the data, 3. Download, 4. Use downloaded tiles">

1. Install the plugin (see [Installing](#installing)) and open it: **Web → LIDAR Downloader UK**, or the toolbar icon. A panel opens on the right, and the 5 km OSGB grid the data comes in appears on the map.
2. **Choose your area**: type a place, postcode or grid reference into **Go to** and press Enter. The map zooms there and the tiles covering it are selected. Or pick tiles on the map with the select icon. To take just your site, tick **Crop to** and choose its boundary layer: the tiles beneath it are selected.
3. **Choose the data**: the menu lists what's available for your tiles. *England Composite DTM, 2022, 1 m* (bare ground, the most complete coverage) is a good start in England; in Wales and Scotland the plugin switches to their own DTM for you.
4. Click **Download** (the button says how many tiles, and roughly how big). When they're downloaded they're added to the project, a layer per tile named after it (SU12NE), coloured by height on one scale; or, as you choose under the button, as one temporary mosaic, or not at all. With **Crop to** ticked, they load instead as one layer cropped to your site.
5. **Create mosaic...** joins the tiles into one mosaic: a temporary one for a quick look, or saved (a VRT, or a GeoTIFF copy) for other software; **Survey dates** switches on (and off) when the ground was flown.

Everything also runs from the **Processing Toolbox** (models and batch jobs). The sections below describe each part in detail.
<br clear="right">

## Usage

**Web → LIDAR Downloader UK → LIDAR Downloader UK** (or the toolbar icon) opens the panel and shows the OSGB 5 km grid. The panel works from top to bottom. Each section folds up when you click its title; the Download folder and Legend start folded, and the panel remembers what you fold.

### 1. Choose your area

**Show OSGB 5 km grid** (at the top) shows the grid the data comes in. The squares are labelled with their tile names when zoomed in (closer than 1:500,000), the tiles you've downloaded of the chosen dataset are shaded (see the **Legend** at the bottom of the panel, and **Downloaded** under the download folder), and squares with no data from any of the sources (most of them sea, or Northern Ireland) have only a faint outline. Ticking the box always brings the grid to the top of the Layers panel. Select tiles with any QGIS selection tool, or use:

- **Go to**: type an OS grid reference (`SU`, `SU12`, `SU 14 27`, `SU 14567 27890`, a tile such as `SU12NE`), an easting and northing (`414360, 130135`), a postcode or a place name, then press Enter. The map zooms there and the tiles covering it are added to the selection (unless there are more than 16: then pick the ones you want). Irish Grid references (`J 33 74`) and Northern Ireland postcodes and places (from OSNI's gazetteer, bundled) are recognised too, and show where to find Northern Ireland's data. Postcodes and places are looked up with [postcodes.io](https://postcodes.io) (Royal Mail and Ordnance Survey open data); grid references are worked out in the plugin.
- **The select icon** (QGIS's own): click a tile on the map to select it, or drag a box to select every tile under it, in place of what was selected. To build up a selection, hold **Shift** or **Ctrl**: with either, a click adds the tile (or removes it if it's already selected); a box adds with Shift and removes with Ctrl. It's how QGIS's own selection tools work, and it works whatever layer is active. Click the icon again to stop picking.
- **The deselect icon** beside it deselects all the tiles.
- **Crop to** a polygon layer (chosen beside it: a site boundary, say, or an area you've drawn in a layer of your own): load, and save mosaics, cut to its polygons (its selected features if it has any). The tiles under it are joined into a mosaic and cropped to its shape.
  - Ticking it selects the tiles beneath the layer: the one tile a crop inside it needs, or the tiles under a larger one (not ones it only touches along an edge).
  - While it's ticked, only those tiles can be downloaded, and they follow the crop: choosing other features of the layer, or editing its shapes, changes them. They can't be picked by hand (the select and deselect icons are greyed out, and Go to only zooms).
  - Untick it to pick tiles, and to load them as downloaded. Point clouds are never cropped: they load whole.

<img src="docs/cropped_dtm.png" width="400" align="right" alt="A 2 m DTM of one tile, cropped to a site boundary (dashed red outline), on the OSGB 5 km grid">

Under them, a line says how many tiles are selected (and how many of them you have already); the **Download** button says how many it will fetch and roughly how big they are.
<br clear="right">

### 2. Choose the data

Until tiles are selected the menu reads *Select a tile to see the data available*: what you can download depends on the tiles. It then lists the datasets available for the selected tiles, named by nation and product: *England Composite DTM*, *Wales DTM (national survey)*, *Scotland DTM* and so on, and what each one is when you hover over it in the menu; for the chosen one, hover over the menu button or click the info icon beside it. Each product opens its survey years and resolutions, with how many of the selected tiles each covers, e.g. *England DTM (individual surveys) ▸ 2012, 1 m (4/6 tiles)*; a product with only one survey is chosen straight from the menu. Where a product has several surveys, **newest for each tile** downloads each tile's newest survey at its finest resolution, and shows everything you've downloaded of that product, shaded in a different colour per survey (with a legend in the panel). The Composite 2022 datasets are always listed for tiles in England. A selection across a border lists each nation's datasets in its own submenu, and England's aerial photos in theirs.

**Also shade tiles available to download** (ticked to start with) shades in blue, as you pan and zoom, the tiles in view that have the chosen dataset but that you haven't downloaded yet. It works with up to 1,600 tiles in view (about 250 × 160 km on a wide screen); its tooltip says how many tiles it shaded, and a note under it appears only when you need to zoom in or the check failed.

If the chosen dataset covers none of the selected tiles but their nation has its own (say England's Composite DTM is chosen and you go to a place in Scotland), the plugin switches to that nation's DTM, DSM or point cloud and says so under the menu. It does this once each time the selection changes, so a dataset you then choose yourself stays chosen.

A few Scottish surveys (Historic Environment Scotland's, and the Phase 2 point clouds) are licensed for **non-commercial use only**: the menu marks them, and the plugin shows their terms and asks before downloading them.

### 3. Download

The **Download** button says what it will do, e.g. *Download 4 tiles (~280 MB)*, and is greyed out until tiles are selected. It downloads the chosen dataset for the selected tiles in the background, three tiles at a time, so you can keep working in QGIS. Progress shows in the panel and the QGIS task bar, and **Cancel** stops it. The plugin checks there's room on the drive first (allowing for zips extracting to about the same again), and asks before a large download or one with tiles you already have. Downloads use your QGIS network settings (proxy, authentication) and retry temporary server errors. If a service isn't answering at all, the plugin says so plainly, naming it, rather than failing tile by tile.

**When downloaded** (the drop-down under the button) says what happens next:

- **Load each tile** (to start with) adds them as soon as they're in, a layer per tile, or one layer of your area when **Crop to** is ticked: download and crop in one go.
- **Load a temporary mosaic** adds them as one mosaic instead: a VRT in QGIS's temporary folder that goes when QGIS closes. It's for two or more selected tiles that make a square or rectangle, so the mosaic has no empty space, and not for point clouds or oblique photos; otherwise (or if one of the rectangle's tiles doesn't download) the tiles load individually.
- **Just download** leaves them in the folder for later.

With **Crop to** ticked, the choice is **Load the crop** (the tiles are still downloaded whole) or **Just download**.

If a download is cancelled or partly fails, or QGIS closes before it finishes, **Resume: N tiles not downloaded yet** appears under the button: it selects those tiles again, in the same dataset and folder, and downloads what's missing.

Downloads are organised like the menu, one folder per survey:

```
<download folder>/lidar_composite_dtm/1m/2022/TQ74NE/TQ74ne_DTM_1m.tif
                  national_lidar_programme_dtm/1m/2020/SU12NW/...
                  lidar_point_cloud/2012/SU12NE/...        (no resolution level for point clouds and photos)
                  scotland_lidar_dtm/0.5m/2021-phase-5/NS79/NS79_DTM_50CM.tif   (a 10 km file, shared by its four tiles)
                  wales_lidar_dtm/1m/2020-2022/SH28SW/...                        (1 km GeoTIFFs)
```

Wales and Scotland publish some surveys as 1 km files and some as one file (or zip) per 10 km square. A 10 km file is downloaded once, into a folder named after the square, and used for each of the four 5 km tiles it covers. The Welsh archive surveys were published with heights in millimetres, and download as they are; when you load them (or make a mosaic of them), the plugin asks whether to convert them to metres, which rewrites the files as GeoTIFFs in metres, or to keep them as published. Each downloaded grid gets overviews (smaller copies, in an `.ovr` file beside it), so a mosaic of many tiles draws quickly when you zoom out (this needs GDAL 3.4 or later, which recent QGIS installers include).

Typical sizes per 5 km tile: 1 m DTM/DSM about 70 MB in England and 36 MB for the Welsh 2020-22 survey, 2 m about 20 MB, and a National LIDAR Programme point cloud about 285 MB. Scotland's portal lists each file's size; the other figures are typical, so a survey that covers only part of a tile downloads less.

### 4. Use downloaded tiles

The buttons work on the selected tiles you've downloaded of the chosen dataset, so they're greyed out until there are some. Whenever the plugin loads something (tiles, a mosaic, survey dates, here or after a download), the grid's tiles are deselected, so the selection doesn't cover what's just been loaded.

- **Load selected** (**Load crop** when **Crop to** is ticked) adds each selected tile to the project as **one layer, named after the tile** (SU12NE), grouped under the dataset's name. A tile already in the project isn't added again.
  - The 5 km tile is the unit: a tile that came as one file is that file, exactly as downloaded; one that came as several (Wales' 1 km grids, aerial photos), or as part of a 10 km file (Scotland, the Welsh archive), is a VRT joining them into the 5 km tile, trimmed to its square (a small file in `<dataset folder>/mosaics/` that reads the downloads on their own pixel grid: nothing is copied or changed).
  - Only how the layers are drawn is set, never the files: elevation (DTM / DSM) is coloured by height on one scale for the whole group, so neighbouring tiles match at their edges (a layer you restyle is left as you set it); imagery has the black area outside the flight made see-through (pixels black in every band; night photos, where black is part of the picture, are shown as they are).
  - With **Crop to** ticked, rasters and imagery load instead as **one layer of your area**: the tiles under it joined into a mosaic cropped to its shape (a small VRT in `<dataset folder>/mosaics/` that reads the files, so nothing is copied). Selected tiles outside the area aren't in it, and if the area misses the tiles they load as downloaded; the message says which.
- **Create mosaic...** joins the selected tiles into one mosaic, cropped when **Crop to** is ticked, and loads it. It asks which: a **temporary mosaic**, for a quick look (loaded straight away, named after its tiles: a VRT in QGIS's temporary folder, gone when QGIS closes, which QGIS marks with its temporary layer icon), or **Save as...** a **VRT** (a small file that reads the tiles, quick to make) or a **GeoTIFF** copy (Cloud-Optimised; standalone, to share or use in other software; made in the background). The name it suggests is made of the data and the tiles, e.g. `England_Composite_DTM_2022_1m_SU12NE_SU12NW.vrt` (`SU12` for all four tiles of a 10 km square, `SU12SW-SU22NE` from corner to corner for more, and `_cropped` when cut to your area). It isn't for point clouds or oblique photos: they're only ever loaded as downloaded. Where surveys overlap (with **newest for each tile**), the newest is on top, at the finest cell size.

  <img src="docs/survey_dates.jpg" width="420" align="right" alt="Survey dates for four Medway tiles, coloured by the year each area was flown (2018 to 2022)">

- **Survey dates** switches on, and off, when the ground was actually flown, for the selected tiles: England's Composite DTM/DSM (and the SurfZone DEM) are merged from many surveys, and each tile comes with a survey file listing them. Each tile's is added as it is, a layer named after the tile in a *Survey dates* group, with one area per survey, coloured (on one scale, so a year is the same colour in every tile) and labelled by year. Where surveys overlap, the newest is drawn on top, as it's the one used in the merged tile. With map tips on (**View → Show Map Tips**) hovering shows the survey and the dates it was flown. The button shows as on while the selected tiles' survey dates are in the project: once they're switched on the tiles are deselected, so to switch them off, select the tiles again and click it (or remove the *Survey dates* group).

**Where the data comes from:** each layer's metadata (**Layer Properties → Metadata**) records the dataset, what it is, its source's copyright statement and licence, ready for attribution in print layouts and reports. Elevation layers (DTM/DSM) are set up for the **Elevation Profile** tool (QGIS 3.26 and later). If the map isn't in British National Grid (EPSG:27700), the coordinate system of the data, the plugin offers once to switch it.
<br clear="right">

### Download folder

The first time, downloads go to `Documents/LiDAR Data`, which the plugin makes for you. Change it here at any time (**Open** shows it in File Explorer). If the folder you chose isn't there (a drive not connected, say), the plugin says so and uses `Documents/LiDAR Data` in the meantime.

**Downloaded** lists every survey in the folder, with its tile count. The grid is shaded for the one chosen there, so you can look at any of your downloads on the grid without selecting tiles first; it follows the dataset you choose in step 2.

In **My downloads...** (below), tick **Delete zips after extracting** to roughly halve disk use. Tiles are still recognised as downloaded without their zip. **Delete extracted zips...**, there too, removes zips already in the folder, but only those whose tiles are fully extracted (every file present at the right size). Nothing happens until you confirm a warning listing the folder, each zip and its size, and any zips that will be kept. Zips are permanently deleted (not moved to the Recycle Bin); tiles can always be downloaded again.

Downloads made by earlier versions (an `extracted` folder, or folders like `..._2009_0.2m`) are moved into the current layout the first time the plugin opens the folder, after asking. Files are only renamed within the folder, never copied or deleted; VRT files in the folder and layers in the open project are updated to the new locations. Other saved projects that use the moved files will need their layer paths updating (QGIS offers this when it opens a project with missing files).

<img src="docs/my_downloads.png" width="480" alt="My downloads: each survey in the download folder with its tiles, size and 10 km squares">

**My downloads...** lists every survey in the download folder, with its tiles, where they are (their 10 km squares; hover to see the tiles) and the space they take, and the space left on the drive. Pick one to **Show on grid** (chooses the dataset and selects its tiles), **Load** it, **Open folder**, or **Delete...** it: that permanently deletes the survey's folder (tiles, zips, and the mosaics and point cloud indexes made from them), after a warning saying how much, and which layers in the project use it (they're removed from the project first).

### Other data

**SurfZone DEM** (*England SurfZone DEM* in the menu, around the coast): one 2 m surface running from the land out across the nearshore sea bed, merging LIDAR with boat surveys (heights above Ordnance Datum). **Survey dates** shows which survey, and which kind (LIDAR, multibeam...), each part comes from.

**River bathymetry** (*England river bathymetry* in the menu, where rivers have been surveyed, e.g. stretches of the Thames): river-bed heights (metres above Ordnance Datum) surveyed by boat with multibeam sonar, as 0.5 m grids covering just the river channel. LIDAR can't see through water, so over rivers the DTM shows the water surface. The grids come without a coordinate system, so the plugin adds British National Grid when extracting them.

**Point clouds** (England's National LIDAR Programme and individual surveys, and Scotland's surveys; they need QGIS's PDAL support, which the standard installers include):

- **Load selected** adds a layer per tile, coloured by classification: ground, vegetation, buildings, water. A tile that came as one file is that file; one of several files is a virtual point cloud of them (QGIS 3.32 or later; before that, a layer per file), and a 10 km file loads once, named after its square. Many Environment Agency surveys record no coordinate system, so the plugin treats them as British National Grid. Point clouds are downloaded and loaded as they are: they load whole, and are never joined into a mosaic or cropped.
- A 5 km tile can be large: the 2017 London survey of TQ16NW is 700 MB (25 files of 1 km).

**Aerial photography** (England, listed after the LIDAR datasets where flights exist):

- *Vertical* orthophotos (RGB, RGBN, IRRGB false-colour infrared, night-time; 10 cm to 1 m) come as ECW files, typically 10-200 MB per tile depending on how much of it the flight covered. A tile comes as several photos (8 to 25 is usual), which load as one layer of the tile, cut at its edges (photos taken across a tile's edge come with both tiles), with the black area outside each photo made see-through (only pixels black in all three colours, so dark pixels stay; photos taken at night are shown exactly as they are, black included). Where photos from two flights overlap, the fuller is drawn on top; a thin seam can show along the edge of a photo there, where its compressed edge isn't quite black. ECW needs QGIS's ECW support, which the standard Windows installers include.
- *Oblique* incident-response photos are angled JPEGs with GPS positions. **Load selected** turns them into a point layer: arrows show the camera direction, and hovering with map tips on (**View → Show Map Tips**) shows the photo.

**Official data sources** (at the foot of the panel) opens each source's own portal: the Environment Agency's survey data, DataMapWales and the Scottish Remote Sensing Portal, and for Northern Ireland, OpenDataNI's LIDAR datasets and the OSNI DTMs.

Results show in the QGIS message bar. Details, such as which tiles failed and why, are in **View → Panels → Log Messages → LIDAR Downloader UK**.

## Processing Toolbox

**Processing → Toolbox → LIDAR Downloader UK → Download LIDAR / aerial tiles** does the same job without the panel, so it works in **models**, **batch mode** and **scripts**:

- **Area:** a layer (tick *Selected features only* to use a selection) and/or an extent. The tiles with data intersecting it are used, up to 300. England, Wales and Scotland are searched as needed; if one service is down, the others' tiles still download and the log says which failed.
- **Dataset:** product, survey year (`latest` = each tile's newest survey; for named surveys type the year as the panel's menu shows it, e.g. `2020-22` or `2011 (Phase 1)`, or just `2011`) and resolution in metres (`1`, `0.5`, `50cm`...; blank = finest available). The Product list is in the menu's order: England, then Wales, then Scotland, then England's aerial photos.
- **Welsh archive heights:** published in millimetres; tick *Convert Welsh archive heights from millimetres to metres* to have them rewritten in metres (off to start with: the files are left as downloaded).
- **Licence terms:** a few Scottish surveys are for non-commercial use only; the tool stops and shows their terms unless *Accept the licence terms...* is ticked.
- **Download folder:** the panel's, to start with. Same layout as the panel, so tiles already downloaded by either are reused (tick *Download again* to refresh them).
- **Build a VRT mosaic** (rasters and imagery). The VRT is added to the project when the algorithm finishes. Left blank, it's saved in the dataset's folder, named after the dataset and the tiles, so each row of a batch run gets its own. Tick **Crop the VRT to the input layer's polygons** to download and crop in one go.
- **Clipped to the input layer** (optional): a standalone GeoTIFF copy of the tiles cut to the input layer's polygons.

From the Python console:

```python
result = processing.run("lidardownloader:downloadtiles", {
    "INPUT": iface.activeLayer(),        # or "EXTENT": "xmin,xmax,ymin,ymax [EPSG:27700]"
    "PRODUCT": 3,                        # index in the Product list: 3 = England National LIDAR Programme DTM
    "YEAR": "latest",
    "RESOLUTION": "",                    # finest available
    "FOLDER": r"C:\Users\me\Documents\LiDAR Data",
    "BUILD_VRT": True,
    "OUTPUT_VRT": r"C:\temp\site_dtm.vrt",
    "CROP_VRT": True,                    # cropped to the input layer's polygons
})
print(result["DOWNLOADED"], result["FAILED"], result["UNAVAILABLE"], result["OUTPUT_VRT"])
```

## Installing

In QGIS, open **Plugins → Manage and Install Plugins**, search for **LIDAR Downloader UK** and click **Install Plugin**. It's listed as experimental for now, so first tick **Show also experimental plugins** under the Plugin Manager's **Settings**.

*Upgrading from "LIDAR Downloader" (before 0.11)?* The plugin was renamed LIDAR Downloader UK and now installs as a separate plugin: uninstall the old one in **Plugins → Manage and Install Plugins**. Your settings and downloads carry over.

Or download `lidar_downloader_uk-vX.Y.Z.zip` from the [latest release](https://github.com/simonstoate/LidarDownloaderUK/releases/latest) and use **Plugins → Manage and Install Plugins → Install from ZIP**.

### From source

Copy (or symlink) the `lidar_downloader_uk/` folder into your QGIS profile's plugin folder:

| QGIS | Windows plugin folder |
|---|---|
| 3.x | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |
| 4.x | `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\` |

Then enable it in **Plugins → Manage and Install Plugins**.

## Development

Contributions are welcome: [CONTRIBUTING.md](CONTRIBUTING.md) explains how to report a bug, suggest a feature or send code.

```
lidar_downloader_uk/       the plugin (this folder is what gets zipped)
  lidar_downloader.py      plugin entry point: panel, buttons, messages
  lidar_downloader_dialog.py, lidar_downloader_dialog_base.ui   the panel's layout
  processing_provider.py   Processing Toolbox algorithm (Download LIDAR / aerial tiles)
  tasks.py                 background QgsTasks: availability search and downloads
  grid.py                  OSGB grid layer: load, style, selection
  maptools.py              the map tool for picking tiles
  places.py                grid references, postcode and place lookups (no QGIS imports)
  pointclouds.py           a tile's point cloud files as one layer: a virtual point cloud (PDAL)
  downloads_dialog.py      the "My downloads" window
  api.py                   products, datasets, Defra search/download API, tile maths (no QGIS imports)
  sources.py               Wales (DataMapWales WFS), Scotland (Remote Sensing Portal API), NI links (no QGIS imports)
  storage.py               download folder layout, zip extraction (no QGIS imports)
  migrate.py               moves downloads from earlier folder layouts (no QGIS imports)
  postprocess.py           GDAL: a tile's VRT, mosaics (VRT, cropped VRT, GeoTIFF copy), overviews, mm to m
  styles.py                elevation colours and the survey dates style
  .flake8                  flake8 settings for the code scan on plugins.qgis.org (the repository's are in setup.cfg)
tests/                     unit tests (pytest) + QGIS smoke test
tools/build_grid.py        rebuilds data/osgb_grid_5km.gpkg: OS grid + where any source has data (run with QGIS Python)
CHANGELOG.md               every release (metadata.txt carries those since the first public release, 0.14.3)
```

Every source file starts with its licence as an [SPDX](https://spdx.dev/learn/handling-license-info/) line (GPL-2.0-or-later) and a docstring saying what the module is for; lines are at most 120 characters.

Unit tests, lint and the security scans that plugins.qgis.org runs on every upload (Bandit and detect-secrets, which
block publication if they find anything) don't need QGIS:

```
pip install pytest flake8 bandit detect-secrets
python -m pytest
flake8 lidar_downloader_uk tests tools
bandit -r lidar_downloader_uk
detect-secrets scan lidar_downloader_uk
```

The smoke test runs the whole plugin headless inside QGIS, with a faked network:

```
"C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat" "<repo>\tests\smoke_test_qgis.py"
```

Set `LIDAR_LIVE=1` to add one real ~70 MB download.

GitHub Actions runs lint and unit tests, plus the smoke test in the `qgis/qgis` Docker images for **3.22, 3.34, 3.40 and 3.44 (Qt5)** and **4.2 (Qt6)**, on every push. To release, add the release to `CHANGELOG.md` and to `metadata.txt` (`version` and `changelog`), then push a matching tag (e.g. `v0.14.3`). The release workflow builds the plugin zip and attaches it to a GitHub release.

Compatibility rules for supporting both Qt5 and Qt6:

- Import Qt only through `qgis.PyQt`, never `PyQt5`/`PyQt6` directly.
- Use fully scoped enums, e.g. `Qt.DockWidgetArea.RightDockWidgetArea` and `QMessageBox.StandardButton.Yes`.
- Use `exec()`, not `exec_()`.
- If a download service changes, update `lidar_downloader_uk/api.py` (England) or `lidar_downloader_uk/sources.py` (Wales, Scotland) only.
- Load icons by file path. There are no compiled `resources.py` files, because `pyrcc` doesn't exist for Qt6.
- Code in `tasks.py` `run()` runs on a worker thread: no widgets or project access there.

## Data sources and licences

LIDAR Downloader UK is an independent plugin. It isn't affiliated with or endorsed by the Environment Agency, the Welsh Government, Natural Resources Wales, the Scottish Government, Ordnance Survey, Ordnance Survey of Northern Ireland or any other organisation whose data or services it uses.

- **England** (LIDAR, river bathymetry, SurfZone DEM, aerial photography) is downloaded from the Environment Agency's [Survey Data Download](https://environment.data.gov.uk/survey) service: © Environment Agency copyright and/or database right, under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
- **Wales** is downloaded from [DataMapWales](https://datamap.gov.wales/) (Welsh Government's 2020-22 national survey; Natural Resources Wales' archive), under the Open Government Licence v3.0. Welsh Government notes the data wasn't created specifically for flood modelling.
- **Scotland** is downloaded from the [Scottish Remote Sensing Portal](https://remotesensingdata.gov.scot/) (Scottish Government and partners). Most surveys are under the Open Government Licence v3.0; some (Historic Environment Scotland's surveys, the Phase 2 point clouds) are for non-commercial use only, and the plugin asks before downloading those.
- **Northern Ireland**'s LIDAR isn't downloaded by the plugin; the **Official data sources** menu links to it on [OpenDataNI](https://www.opendatani.gov.uk/search?q=lidar). Place names for Northern Ireland come from OSNI's gazetteer (bundled; Open Government Licence v3.0).

  The plugin records each survey's source and licence in the loaded layer's metadata; please acknowledge it in maps and reports.
- **The OSGB 5 km grid** bundled with the plugin (`lidar_downloader_uk/data/osgb_grid_5km.gpkg`: every square of the British National Grid, sea included) is Ordnance Survey's [osbng-grids](https://github.com/OrdnanceSurvey/osbng-grids): contains OS data © Crown copyright and database right, under the Open Government Licence v3.0. Its `HAS_DATA` flag (whether any of the sources had data for the square) was added from the three services by `tools/build_grid.py`, which can be re-run to refresh it. It's only used to draw squares without data faintly, and to leave them out when picking with a box, cropping or going to a place: what each tile has is always checked with the service when you select it. See `lidar_downloader_uk/data/LICENSE-DATA.txt`.
- **Place and postcode search** sends the text you type to [postcodes.io](https://postcodes.io) (Royal Mail and Ordnance Survey open data). Grid references are worked out in the plugin and not sent anywhere. Nothing else leaves your computer apart from the searches and downloads on the England, Wales and Scotland services.

Tested on Windows (QGIS 4.2) and Linux (QGIS 3.22, 3.34, 3.40, 3.44 and 4.2 in CI); it should work on macOS but hasn't been tried there, so please report any problems.

## About

I've worked in flood risk, drainage and natural flood management for more than twenty years, and I'm passionate about putting the growing amount of open data to practical use. I built this plugin with AI assistance, to make the UK's open LIDAR quick to find, download and use in QGIS. I hope you find it useful, and I'd love to hear what you think.

**Simon Stoate**

## Licence

Copyright (C) 2025-2026 Simon Stoate. GPL-2.0-or-later. See [LICENSE](LICENSE).
