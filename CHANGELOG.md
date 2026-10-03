# Changelog

All notable changes to LIDAR Downloader UK, newest first. 0.14.3 is the first public release: the changelog in the plugin's `metadata.txt` (shown in QGIS's Plugin Manager and on plugins.qgis.org) starts there, and the versions before it are the plugin's development history.

## 0.14.6

Changes from the first live use, to make the plugin simpler and keep the data exactly as downloaded.

- **Tiles load as downloaded:** each 5 km tile loads as one layer named after it, read straight from the downloaded files. Only "Create mosaic" and "Crop to" join tiles, and point clouds are always loaded as they are.
- **A simpler, shorter panel:** QGIS's own select and deselect icons, a data menu that waits for a tile (each dataset described in its tooltip), a Download button that says what it will fetch, sections that fold up, and the grid deselected once something loads.
- **More choice when downloading:** load each tile, load a temporary mosaic, or just download; "Create mosaic" makes a temporary mosaic or a saved file named after the data and the tiles.
- **Crop to your site:** tick "Crop to" beside a polygon layer and only the tiles it needs are downloaded, then loaded as one cropped layer.
- **Fixes:** the data menu follows the nation, Northern Ireland's links work again, night aerial photos show properly, Welsh archive heights are converted only if you agree, and a download no longer occasionally stays busy until cancelled.

## 0.14.5

Metadata follows plugins.qgis.org's rules for QGIS 4: the `supportsQt6` flag is gone, as QGIS now takes QGIS 4 support from `qgisMaximumVersion` (4.99) alone and the site warns about the flag on upload. Nothing else changes. QGIS 3 (Qt5) and QGIS 4 never read the flag; the only builds that still do are the Qt6 previews of QGIS 3.40 and 3.44 (such as OSGeo4W's `qgis-ltr-qt6` and `qgis-qt6` packages), which now treat the plugin as incompatible unless `QGIS_DISABLE_SUPPORTS_QT6_CHECK` is set.

## 0.14.4

Passes the Qt6 compatibility check that plugins.qgis.org runs on uploads (QGIS's pyqt5_to_pyqt6 script): the fallbacks for older QGIS 3 versions (label placement, geometry type, Processing source type, raster statistics) name their enums in full, as Qt6 requires, instead of the short Qt5 form. Those lines never run on QGIS 4, so nothing changes in use; an unused third fallback for the raster statistics is gone.

## 0.14.3

First public release (for plugins.qgis.org). "Shade tiles that have the chosen data" works with up to 1,600 tiles in view (it was 400), about 250 x 160 km on a wide screen, and a note under its tick box says when to zoom in, while it's checking, if the check failed, and how many tiles it shaded (only the legend said any of this before, and a failed check said nothing).

## 0.14.2

Prepared as the first public release, but not published (0.14.3 was): the changelog in the plugin's metadata starts afresh, with this history kept in this file. The plugin is the same as 0.14.1.

## 0.14.1

Tidy-up for publishing, from a review of the code as an open-source project: file headers with the licence, clearer comments, unused code removed and long functions split. Fixes: a server error from the postcode search no longer reads "no data available"; the Processing tool's messages can be translated.

## 0.14.0

Easier from the first click. The panel is laid out as the steps you take: 1. Choose your area, 2. Choose the data, 3. Download, 4. Use downloaded tiles (then the download folder). The grid shows when the panel opens, and the first download folder (Documents/LiDAR Data) is made without asking. Datasets are named by nation and product ("England Composite DTM", "Wales DTM (national survey)", "Scotland DTM"), with the year before the resolution ("2022, 1 m"), and "newest for each tile" replaces "latest available" and "all years and resolutions". A product with one survey is chosen straight from the menu, a selection across a border lists each nation separately, and non-commercial surveys are marked. If the chosen dataset covers none of the selected tiles, the panel switches to their nation's own (e.g. Scotland DTM) and says so. Size estimates use each file's real size where the service gives it (Scotland) and typical sizes for Welsh files, counting a 10 km file once. Unfinished downloads (cancelled, partly failed, or QGIS closed) can be resumed. Downloaded grids get overviews, so big mosaics draw quickly zoomed out. My downloads says where each survey is (its 10 km squares). Processing: the Product list follows the menu (England, Wales, Scotland, then aerial photos), so products from 12 on have new numbers; the download folder starts as the panel's; resolutions such as "1 m" or "50cm" are understood; a VRT left unnamed is named after its tiles (one per batch row).

## 0.13.0

Correct data and a clean security scan, from a full review before publishing: Welsh archive (NRW) heights were stored in millimetres and loaded 1000x too high: they're now converted to metres (downloads made earlier are converted when next used). Mosaics of mixed surveys ("latest", "all years", Processing) now show the newest survey where they overlap, at the finest cell size (an older 10 km file could hide a newer survey; cell sizes were averaged). Cropping: trimmed to the tiles (far-apart sites no longer make a vast file), self-crossing polygons repaired, an area away from the tiles loads uncropped and says why, aerial photos keep black as transparent, and a missing crop area never stops loading. A file that's loaded is released (after asking) before being replaced; cropped point clouds are cancellable and load without a second copy. A service is only treated as down after two tiles get no answer, and the reason is kept. The Processing tool no longer connects signals across threads. The grid only ever takes over its own layer. Security scan (Bandit, detect-secrets): clean.

## 0.12.2

When a service isn't answering (the Environment Agency's, DataMapWales or the Scottish portal), the plugin says so plainly, naming it ("... isn't responding at the moment. Try again later"), once rather than as a failure per tile. It stops after two unanswered tries and skips that service's other tiles, instead of each tile waiting to time out.

## 0.12.1

Unticking "Show OSGB 5 km grid" removes the grid again in a project saved with it: the plugin takes over the saved grid (ticking the box to match) instead of adding a second one.

## 0.12.0

One job, done well: the plugin now finds, downloads, organises and loads the data. Styling (hillshade, contours, your own styles), terrain and contour exports, differences, surfaces from point clouds and 3D terrain are moving to a companion plugin. New: "Crop to this layer" crops what's loaded, and mosaics you save, to your drawn area or site boundary; with "Load tiles after downloading", download and crop in one go (and in the Processing tool: "Crop the VRT"). Tiles load as one seamless mosaic per dataset, coloured by height. "Create mosaic..." saves a VRT or a GeoTIFF copy (or for point clouds a virtual point cloud, or a cropped COPC file). Clear also removes the drawn area; the buffer is gone. Loading more tiles into a group keeps its attribution.

## 0.11.0

Renamed LIDAR Downloader UK (plugin folder lidar_downloader_uk; repository LidarDownloaderUK). If you have the older "LIDAR Downloader" installed, uninstall it: settings, styles and downloads carry over. Review fixes: a service that doesn't answer no longer hides the others' results; long Welsh / Scottish result lists are fetched in pages; a tile whose files only partly downloaded, or a 10 km file listed for another tile of its square, no longer counts as downloaded; the Processing tool asks you to accept non-commercial licence terms, accepts the survey year as shown ("2020-22", "2011 (Phase 1)" or "2011") and says when a service is down; non-commercial surveys record their own licence in layer metadata; downloads stay on the official services (redirects checked) and folder names from the services are checked; zips that won't fit on the drive aren't extracted; places ranked cities first rather than England first; wording no longer says England only.

## 0.10.0

Wales and Scotland. Wales from DataMapWales: the 2020-22 national survey DTM/DSM (1 m) and NRW's archive surveys (1998-2015). Scotland from the Scottish Remote Sensing Portal: Phase 1-6 DTM/DSM and point clouds and the other surveys it publishes, listed by survey name. 10 km files are downloaded once and shared by their four tiles; surveys licensed for non-commercial use ask first. Northern Ireland: Irish Grid references, NI postcodes and places go to the right place and point to the official sources (OpenDataNI, OSNI DTMs, Flood Maps NI); "Official data sources" menu in the panel. The grid's data flag now covers all three sources.

## 0.9.5

The grid is now the whole Ordnance Survey 5 km grid (osbng-grids), marked with where the Environment Agency has data, instead of land squares marked by country: coastal and estuary tiles that were missing or greyed out (e.g. Norfolk, Holderness, Morecambe Bay, the Severn, SurfZone tiles offshore) can now be selected and downloaded. Tiles are no longer skipped for being outside England: the live check decides. New: coastal bathymetry (can be burnt into a DTM export), IRRGBN aerial photography, oblique photos from other surveys.

## 0.9.4

Audit fixes: an export no longer deletes files of another dataset with the same name (a shapefile's .dbf / .shx when writing rivers.asc), and refuses a name whose .prj would change. Downloads only follow links on the Environment Agency service, for valid tile names. Licence and the OS grid's data notice (OGL) ship with the plugin; the grid credits OS.

## 0.9.3

The Processing download tool can style its VRT / clipped raster and make contours (interval, smoothing; GeoPackage, Shapefile or DXF). Help and guide link in the panel and the Processing tools. Section 3 buttons two to a row, so the panel fits narrower docks. No more QGIS 4 deprecation warning from the colour stretch. README: getting started, screenshots.

## 0.9.2

My downloads: every survey in the download folder with its size and the free space; show it on the grid, load it, open its folder, or delete it (with a warning). A disk space check before downloading; tiles download three at a time. SurfZone DEM (the coast from land out across the sea bed), with survey types in Survey dates.

## 0.9.1

River bathymetry: river-bed heights surveyed by boat (multibeam sonar), where LiDAR only sees the water surface. Listed in the Dataset menu where surveyed (e.g. the Thames), in British National Grid. Export terrain / contours from a DTM offer to burn the river bed in.

## 0.9.0

Point clouds (QGIS 3.32+ with PDAL): the tiles in a group load as one virtual point cloud, coloured by classification and in British National Grid (many EA surveys record no coordinate system). Export terrain on a point cloud makes a DTM (ground points) or DSM (first returns) at your cell size, filling small gaps; Clip to layer clips the points. Both are also Processing algorithms.

## 0.8.2

Contour smoothing (Styles menu and Export contours): None, Light or Strong. The surface is smoothed before contouring, which takes out the ragged rings on flat ground. "Use as 3D terrain" (right-click a raster in the Layers panel, or the Styles menu) makes a DTM the project's terrain for 3D map views; it asks before replacing an existing terrain. A note when contours will take a while.

## 0.8.1

Difference: the chosen DTM/DSM minus another surface downloaded for the selected tiles, e.g. a newer survey minus an older one (change in ground level) or a DSM minus a DTM (heights of buildings and vegetation). Styled blue to red around zero, with the volume gained and lost reported. Also a Processing algorithm ("Difference between surfaces").

## 0.8.0

Export terrain: one grid of the selected DTM/DSM tiles, clipped to a layer plus a buffer, resampled to a cell size, as GeoTIFF, ESRI ASCII (.asc), ESRI float (.flt) or XYZ, with one no-data value. With "all years and resolutions", older surveys fill gaps in the newest. Export contours: GeoPackage, Shapefile or DXF, as 3D lines at their elevation (index contours on their own CAD layer). Both are also Processing algorithms (models, batch).

## 0.7.1

Go to: an OS grid reference, easting/northing, postcode or place name zooms there and adds the tiles covering it (postcodes and places from postcodes.io). Contour interval: 0.5, 1, 2 or 5 m (Styles menu), labelled every fifth contour. Loaded layers record the Environment Agency attribution and Open Government Licence in their metadata; elevation data is set up for the Elevation Profile tool (QGIS 3.26+). A short description of the chosen dataset; an offer to switch the map to British National Grid.

## 0.7.0

Pick on map: click tiles to add or remove them, drag a box to add the tiles under it. Draw area: a rectangle or polygon, with an optional buffer, selects the tiles under it and is kept as a "Drawn area" layer ready for Clip to layer. Show coverage: shades the tiles in view that have the chosen dataset but aren't downloaded. Survey dates: a layer of when each part of the selected Composite tiles was actually flown, coloured by year, with the flown dates in its map tips.

## 0.6.3

Styles are applied to a mosaic of all the tiles loaded into a dataset's group, so the colour stretch uses the whole group's min and max and hillshade/contours are seamless across tiles; loading more tiles updates the mosaic and its styled layers. Contours are now real contour lines (generated in the background) with the 5 m index contours labelled with their elevation.

## 0.6.2

Styles: tick one or more styles to apply when tiles or VRTs are loaded. Built-in: Hillshade (z-factor 5, multidirectional, multiply blending), Contours (1 m, 5 m index) and Elevation colours. Add your own: save the active layer's style, or add a .qml file. Several styles load as a styled copy each, in sub-groups (contours on top). The styles can also be applied to the active layer. Replaces the Hillshade... button.

## 0.6.1

The legend has its own section at the bottom of the panel. Fixed crashes on QGIS 3.x: choosing a dataset rebuilt the menu from inside its own click handler, and download progress used Python callbacks connected in worker threads.

## 0.6.0

Choose a whole product ("all years and resolutions"): the grid is shaded in a different colour for each survey (year / resolution) you've downloaded, with a legend in the dock; downloads fetch each tile's newest survey at its finest resolution. New folder layout following the Dataset menu: <product>/<resolution>/<year>/<tile>/ (e.g. lidar_composite_dtm/1m/2022/TQ74NE/). Downloads in the old layout are moved into it after asking; VRTs in the folder and layers in the project are updated to match. The download folder is filled in as soon as QGIS starts, so it no longer looks forgotten after a plugin update.

## 0.5.5

Dataset menu lists only products at the top level; each opens its resolutions and survey years (a resolution with several years has its own sub-menu). The product holding the current choice is shown in bold.

## 0.5.4

Dataset list grouped: one entry per product and resolution, with survey years (and "latest available") as a sub-menu where there are several. Layers already in the project aren't loaded again (tiles, VRTs, hillshades, photos). Ticking "Show OSGB 5 km grid" moves the grid to the top of the Layers panel; dataset groups are added just below it. "Load tiles after downloading" moved to the bottom of the Download section.

## 0.5.3

Hillshade... and Clip to layer... in the dock: Cloud-Optimised GeoTIFFs made in the background from the selected tiles and loaded into the project. "Load tiles after downloading" option. The Processing algorithm can also output the tiles clipped to the input layer and a hillshade, so a model can go straight from an area to a clipped hillshade. The dock scrolls on short screens.

## 0.5.2

Processing Toolbox algorithm "Download LIDAR / aerial tiles" (LIDAR Downloader group): area from a layer and/or extent, product, year or "latest", resolution or finest, optional VRT mosaic. Works in models, batch mode and PyQGIS scripts, and shares the dock's download folders.

## 0.5.1

Aerial photography where it exists: vertical orthophotos (RGB, RGBN, IRRGB, night-time; ECW) load as rasters with transparent borders and can go in a VRT; oblique incident-response photos load as points from their GPS tags, with arrows showing the camera direction and the photo in the map tip. Imagery is listed after the LiDAR datasets.

## 0.5.0

Choose what to download: a Dataset list shows every LiDAR product, survey year and resolution available for the selected tiles (from the Defra search service), e.g. National LIDAR Programme DTM/DSM/VOM/intensity/point cloud and single surveys back to 1998. "Latest available" option downloads each tile's newest survey in one go. Each dataset downloads to its own sub-folder (the Composite DTM 2022 1m keeps the main folder, so earlier downloads are still found). Point clouds (.laz) load as point cloud layers; loaded tiles are grouped by dataset. Download size estimates per dataset; lower memory use for large tiles.

## 0.4.6

New icon: a map tile with LiDAR-scanned terrain and a download badge (SVG, sharp at any size).

## 0.4.5

"Delete extracted zips..." deletes permanently instead of using the Recycle Bin; the warning says so clearly (tiles can be downloaded again).

## 0.4.4

Grid squares are labelled with their tile name (e.g. TQ77SW) when zoomed in. New "Open" button opens the download folder in File Explorer. "Tidy up" is now "Delete extracted zips...": a warning lists the folder, every zip to be deleted (with sizes) and any that will be kept; Cancel is the default; files go to the Recycle Bin instead of being permanently deleted.

## 0.4.3

Fix: QGIS 4 listed the plugin as incompatible and wouldn't enable it (metadata lacked qgisMaximumVersion, so QGIS assumed 3.99).

## 0.4.2

New "Tidy up" button: deletes zip files already in the download folder whose tiles are fully extracted (checked file by file), after asking.

## 0.4.1

New option: "Delete zip files after extracting" to save disk space. Tiles whose zip was deleted still count as downloaded.

## 0.4

Redesigned dock that resizes properly, laid out as 1. Choose / 2. Download / 3. Use. New "Tiles in map view" and "Tiles under layer" selection buttons. Download size estimate; tiles outside England (no data) are greyed and skipped. Results appear in the QGIS message bar and Log Messages panel instead of pop-ups. Download folder is remembered as soon as it's changed. The grid stays when the dock is tabbed behind another panel. Grid now ships as a single GeoPackage.

## 0.3

Downloads run in the background (QGIS task manager): QGIS stays usable, progress shows MB downloaded, and a Cancel button stops them. Uses the QGIS network settings (proxy, authentication). Retries temporary server/network errors automatically. One question up front for already-downloaded tiles instead of one per tile. Code split into modules; new automated tests on QGIS 3.40, 3.44 and 4.2.

## 0.2.1

Downloads work again: switched to the current Defra endpoint (environment.data.gov.uk); the old api.agrimetrics.co.uk host was retired. Downloads stream to disk; failure reasons shown per tile.

## 0.2

Support Qt6 builds (QGIS 4) as well as Qt5 (QGIS 3.22+). Fix downloaded tiles not being recognised from their zip files. Fix errors when the grid layer is removed manually. Keep the "Show OSGB Grid" checkbox in sync with the layer. Clean up properly when the plugin is unloaded or reloaded. Progress bar now advances for failed tiles too. Failed or corrupt downloads are no longer treated as "already downloaded".

## 0.1

Initial release.
