# COS Neptune — COS 3.1 "Neptune"

The Aero release of COS. Everything is drawn by Neptune itself through **GDR
(Graphics Desktop Rendering)**. There's no curses underneath, and no X or
Wayland needed: Neptune becomes DRM master and composes every pixel itself.

## Run it

| Where | Command |
|---|---|
| Real console (Ctrl+Alt+F3, log in) | `python3 ~/cos/neptune/neptune.py` → full GDR |
| Inside your desktop session | `python3 ~/cos/neptune/neptune.py` → opens in a window |
| Force a mode | `--gdr`, `--fb`, `--window [--fullscreen] [--size 1366x768]` |
| Pick the GDR resolution | `--mode 2560x1440` or `--mode 3840x1080@60` |
| Skip boot screen | `--no-boot` |
| Screenshot | `--shot out.png` |

**Emergency exit:** Ctrl+Alt+Backspace.

## GDR safety net (same idea as COS v2.4's beta)
1. **Watchdog.** You get 20 s after the desktop appears to press a key or
   click. If you don't, Neptune gives the console back on its own.
2. **Attempt marker.** `home/.neptune/gdr-attempt` records a start that never
   got confirmed. Next time Neptune falls back from DRM to `/dev/fb0`, and
   after that it refuses until you pass `--retry-gdr`.
3. **By hand.** From another VT run `pkill -f neptune.py`. The console is
   restored on exit, on atexit and on SIGTERM/SIGHUP/SIGINT.

## What's in it
- **Aero Glass**: blurred, tinted, translucent window frames, menu bar,
  taskbar, Start menu, menus and thumbnails. There are 16 glass colours plus
  intensity and transparency settings.
- **Aero Snap / Peek / Shake / Flip (Alt+Tab)**, taskbar hover thumbnails
  with peek, and open, close, minimize and maximize animations.
- **Shell**: COS menu bar (COS FILE EDIT APPS CONTROL POWER HELP), Superbar
  taskbar with the Neptune orb, Start menu with search, desktop icons, an
  analog clock gadget, notification balloons, and boot, welcome and shutdown
  screens.
- **Apps**: Explorer, Notepad, Terminal (real pty, xterm colours),
  Calculator, Paint, Photo Viewer, Personalize, System (Neptune Experience
  Index), Help, Date and Time, Run, About, and the COS runtime-error dialog
  from the concept art, which appears whenever an app crashes.

## Back from COS v2.4
- **Task Manager** (Ctrl+Shift+Esc): Applications, Processes (sort, End
  Process) and Performance (a graph for every core, plus memory).
- **Druman**: the music and video player. ffmpeg decodes the video into the
  window, ffplay plays the audio, and music gets a numpy spectrum visualiser.
  Explorer opens audio and video files with it.
- **.nepapp apps**: the .cosapp system again. Drop `Name.nepapp` into
  `home/.neptune/apps/` and it's in APPS and Start, hot-reloaded when you
  save. Manage them in COS > App Manager. `Bubbles.nepapp` is the sample.
- **Lock screen** (Super+L): optional password, optional at startup, set in
  CONTROL > Lock Screen.... It locks the look, not real security.

## Handing the screen to another program (GDR)
In GDR an app can set `shell.suspend_request = {'title', 'cmd', 'log', 'cwd', 'env', 'done'}`
(when `shell.can_suspend` is true, i.e. only in GDR). Neptune saves a picture of
the screen and its window layout (`home/.neptune/suspend/`, private), lets go of
the display, input and console, runs `cmd` until it exits, takes the screen back,
puts the old pixels up at once, and calls `done(returncode)`. Its process stays
alive, so every window comes back exactly as it was; if Neptune is killed
meanwhile, the next start reopens the saved windows. LEIGON uses this to run its
native engine (`leigon --kms`) or a private X server. `nep/suspend.py` and
`gdr_suspend()` in `neptune.py` came from the LEIGON session on brainpad.

## Webinator, Pigeon, build numbers
- **Webinator** (v2.4's browser, now graphical): Neptune's own HTML engine in
  `nep/web/` with fonts, images, tables (the auto width algorithm), lists,
  GET/POST forms, inline + linked CSS for colours and hiding (`A B`, `A > B`,
  `.a.b`, `#id`), favourites, find, zoom, view source. Non-page links
  download to `home/Downloads`. A `.cip` offers to install.
- **Webinator JavaScript**: each page with scripts runs in its own Deno
  process (`nep/web/jsengine.py` + `nep/web/jsrt/runtime.js`) allowed the
  network and nothing else. The runtime has its own DOM, events, history,
  fetch/XHR/WebSocket; the DOM comes back as HTML with `data-nep` ids and
  clicks/typing go back by id. React apps (TCFNet) work. Site storage and
  cookies: `home/.neptune/web/storage/`; script console:
  `home/.neptune/web/js-console.log`; module cache: `cache/deno`. Toggle in
  Webinator's Tools menu. Needs `~/.deno/bin/deno` (or `deno` on PATH).
- **EarthDog** (Mozilla Firefox for COS Neptune, `nep/web/earthdog.py`): the
  engine for real websites. Webinator (and CEAP:Web through it) shows every
  http/https page with it: one headless Firefox in the background, driven
  over WebDriver BiDi on a local socket; frames are drawn into the page area,
  clicks/keys/wheel go back as input actions. YouTube, claude.ai, logins,
  sound, popups (own window), alerts (COS dialogs), downloads (to
  `home/Downloads`) all work. Profile: `home/.neptune/earthdog/profile`; log:
  `home/.neptune/earthdog/earthdog.log`. Firefox starts on first use and
  stops 2 minutes after the last page closes. The built-in engine still does
  `about:`/`file:` pages, and http(s) too if Firefox is missing, fails, or
  Tools > "Use EarthDog (Firefox)" is off (setting `webinator_engine`).
  Command line: `bin/earthdog text|html|title|shot URL` (own temp profile).
  Firefox is used as it is (its own user agent and automation marker).
- **Pigeon**: v2.4's LAN folder share, same protocol (`/token/`,
  `manifest.json`, `pull`), so Neptune and v2.4 sync either way. The Receive
  tab mirrors a share into `home/Downloads/Pigeon/<host>`, moving only changed
  files.
- **Build numbers**: `<general version><revision><YYYYMMDDHHMMSS>`, e.g.
  `3020261031201548` = COS 3.0 built 2026-10-31 20:15:48. The time is the
  newest Neptune source file's, so it only changes when the code does. It
  shows on the desktop (CONTROL > Show Build Number on Desktop), the boot
  screen, About and System Information.

## New in 3.1
COS 3.0 is kept, runnable, in `old/v3-0/`.
- **On the CEAP engine:** File Explorer (CEAP:Main, now what "files" opens),
  CEAP:Web (the browser in the Exploring Space, with a JS console; Webinator
  itself is embedded unchanged), and the **Control Panel** (`nep/apps/control.py`),
  which holds every COS 2.4 and Neptune setting, searchable, with v2.4-only
  console settings pointed at COS v2.4 Mode. Control Panel > Programs switches
  Explorer and the browser back to Classic.
- **CEAP App Maker** (`nep/apps/ceapmaker.py`): build `.ceap` apps by clicking.
- **COS v2.4 Mode mouse:** Neptune's terminal now speaks xterm mouse reporting
  (1000/1002/1003, SGR 1006), so cos.py (and mc, htop, vim) get real clicks,
  drags and the wheel.
- **Plug-and-play:** GDR input re-scans every 2 s and drops unplugged devices
  (releasing stuck modifiers); `.cdi` drivers start and stop as their devices
  come and go, with Device connected / disconnected balloons.
- **Tab navigation** in every window (App.focus_step, dotted focus ring).
- **Emergency exit:** Ctrl+Shift+Alt+Q, works from anywhere (lock screen,
  screen saver); setting `emergency_exit`.
- **Recycle Bin** (`nep/trash.py`, `nep/apps/recycle.py`): Delete in Explorer moves to
  `home/.neptune/trash/` with restore; Shift+Delete deletes for good.
- **The COS Place** (`nep/cosplace.py`, `nep/themes.py`, `nep/apps/cosplace_app.py`): peer-to-peer
  sharing of themes/pictures/videos/music/apps, protocol v1 shared with COS Aurora
  (`~/cos/codename_aurora/docs/COS_PLACE.md`): HTTP on 8643 (falls back to 8645-8651 if
  taken), UDP discovery on 8644. Off until the user joins (`cosplace_join`); downloads are
  hash-checked, kept on the shelf (`home/.neptune/cosplace/`) and never run. `.ctheme`
  themes live in `home/.neptune/themes/`; double-clicking one opens The COS Place.
- **COS Cities** (`nep/cities.py`, `nep/apps/cities_app.py`): old-web home pages. One site per
  computer in `home/Sites/` at `/cities/<Neighborhood>/<1000-9999>/`, served by the COS Place
  node; includes `<!--#counter-->`, `<!--#guestbook-->`, `<!--#webring-->`, `<!--#updated-->`;
  generated clip art at `/cities/_art/`. Directory via `GET /cosplace/v1/sites` on each peer
  (Neptune extension; other nodes 404). No scripts: .js not served, `<script>`/`on*=` stripped,
  CSP `script-src 'none'`. Guestbook escaped, 500 chars, one entry per address per 30 s.
- **Fun:** Bubbo the desktop pet (`nep/fun.py`), tip of the day, and a secret.

## CEAP (COS Explorer Application Platform)
- **CEAP:Main** (`nep/apps/ceap.py`, desktop icon CEAP): Back/Forward/Up/Refresh,
  address + search, Directory tree, the Exploring Space in Icon.Arrange (drag
  icons anywhere; positions saved in `home/.neptune/ceap/arrange.json`),
  Icon.Standard and Text.Display modes, Overview (Text.over / Dir.over /
  image / archive / CEAP app) and Metadata panes, and Code.Exe.Explore, a
  docked terminal that follows the folder. File operations come from Explorer.
- **CEAP apps**: `.ceap` text files (type CEAPApp) made of `win.CEAP:Note`
  (text, input, button -> actions) and `win.CEAP:Main` (root, view, terminal)
  blocks. Apps in `home/.neptune/ceap/` appear in APPS (live reload); any
  `.ceap` runs from Explorer or CEAP. Language: Help > CEAP. Sample: Hello.ceap.

## Bubble backgrounds and screensavers
- **Desktop Background > Bubbles**: COS Bubble, COS Multi-Bubble (the COS 3.1
  designs, redrawn in code so they're sharp at any size and widen properly
  on ultrawide screens), Bubble Field, Bubble Sunset, Bubble Night, Bubble
  Mint. The original 1280x720 pictures are under Picture.
- **Screensavers** (`nep/savers.py`, CONTROL > Screen Saver...): Bubbles,
  Bouncing COS, Warp, Aurora, Channel Rain, Clock, Blank. Settings:
  `screensaver`, `saver_wait` (minutes), `saver_lock` (show the lock screen
  on resume). The shell has a `saver` phase; a 12 px mouse nudge or input in
  the first 0.8 s doesn't end it; apps with `inhibit_saver` (a playing video
  in Druman) hold it off.

## Zip, unzip, gzip, gunzip
- **Archiver** opens `.zip`, `.tar.gz`/`.tgz`, `.tar.xz`, `.tar.bz2`, `.tar`,
  and single `.gz`/`.bz2`/`.xz` files like a folder: browse inside, open a
  file straight from it, extract all or some, test the checksums, add files
  to a `.zip`. Downloads of archives in Webinator open in it.
- **Explorer**: right-click an archive for Extract All..., Extract Here
  (straight in when there's one top folder, otherwise into a new folder) or
  Decompress (ungzip); right-click anything for Send to > Compressed (zipped)
  folder / TAR.GZ / GZip, or Compress... (ZIP, TAR.GZ, TAR.XZ, TAR, GZIP, XZ;
  store to smallest).
- Engine: `nep/archive.py`, stdlib only. Extracting refuses paths that escape
  the folder, never creates links or devices, caps total size against free
  disk (zip bombs), and writes only inside `home/`.

## Compatibility mode, packages and drivers
- **Compatibility mode** runs COS 2.x programs (`.cosapp`) inside Neptune with
  a built-in copy of the v2.4 `cos` API, drawn as console cells in an Aero
  window. Your existing `~/.cos/apps` programs show up in APPS by themselves.
  Right-click a running one for **Compatibility settings**: COS 2.4 or 2.3
  (keyboard only), any of the 7 v2.4 colour schemes, 80×25 text mode, a CRT
  effect, and text size. A fatal error blue-screens only that program.
- **COS v2.4 Mode** (like Windows XP Mode) runs the real `~/cos/cos.py` in a
  window, sandboxed with bubblewrap: no access to `/dev/input`, its own home
  in `home/.neptune/cosmode/`, never touches your real `~/.cos`.
- **.cip / .cdi packages**: same format and rules as v2.4 (`~/cos/PACKAGES.md`).
  Double-click one, or use APPS > Install Package. Uninstall from CONTROL >
  Programs and Features. A `.cip` may also carry `.nepapp` apps. Right-click
  a folder with a `package.json` > Build package (saved to Documents).
- **Drivers**: `.cdi` drivers run with the v2.4 driver API. Their keys,
  clicks and pointer moves become real Neptune input. CONTROL > Device
  Manager lists every device and its driver. `NEPTUNE_NO_DRIVERS=1` skips them.

## Layout
```
neptune.py        entry point, backend choice, watchdog
nep/display.py    GDR: DRM/KMS dumb buffer, fbdev fallback, pygame window, headless
nep/evinput.py    evdev keyboard / mouse / touchpad (tap-to-click, 2-finger scroll)
nep/gfx.py        glass, blur, shadows, gradients, cached glow/shadow text
nep/shell.py      compositor + window manager + shell
nep/ui.py, app.py Aero widget toolkit and app base class
nep/apps/         the programs (taskmgr, druman, plugins, lockscreen, ...)
nep/apps/samples/ sample .nepapp copied into home/.neptune/apps on first run
nep/compat/        COS 2.x compatibility: curses shim, v2.4 cos API, host window
nep/archive.py    zip/tar/gzip/bzip2/xz engine   nep/apps/archiver.py  Archiver UI
nep/packages.py   .cip/.cdi installer      nep/drivers.py  device scan + .cdi driver host
home/             Neptune's own home (Documents, Pictures, settings, logs)
claude/           copy of the old Claude session data
```

Neptune only writes inside `~/cos/neptune`. Explorer can browse the rest of
the disk read-only.

## Screenshots, backgrounds, Aero on/off, upgrading from v2.4
- **Print Screen**: PrtSc saves the whole screen, Alt+PrtSc the active window,
  to `home/Pictures/Screenshots/` (also COS > Take Screenshot).
- **Desktop Background** (CONTROL > Desktop Background..., or right-click the
  desktop): Neptune wallpapers, original Nostalgia designs (Teal Classic,
  Rolling Hills, Starfield, Sunset Grid, Blueprint, Beveled Tiles, COS Druman,
  Marble), solid colours, gradients, 10 patterns, your own text, or your own
  picture (fill/fit/stretch/center/tile; Neptune keeps a copy in
  `home/.neptune/wallpapers/`).
- **Aero on/off**: Personalize > "Neptune Basic" (or CONTROL > Aero Glass) for
  slow computers: no glass, blur or shadows, and animations off.
- **Upgrade from COS v2.4**: `~/cos/pkgs/COS_Neptune_Upgrade.cip`. Install it in
  v2.4 (APPS > Install Package), then run APPS > Upgrade to COS Neptune. It
  installs Neptune next to v2.4 (v2.4 keeps working), checks the computer
  first, brings over IBBS settings, sound, wallpaper and bookmarks, and adds a
  COS Neptune launcher. Rebuild it after changing Neptune with
  `python3 tools/make_upgrade_package.py`.

## Set Up COS Neptune (OOBE)
The first time Neptune starts on a new install it runs **Set Up COS Neptune**
after the boot screen: your name and the computer's name, an optional password
(and whether to ask for it at start-up), Aero or Basic (Setup times this
computer and recommends one), glass colour, desktop background, clock, sounds
and motion, and, if COS v2.4 is on the machine, which v2.4 settings to bring
over. Look choices apply live behind the wizard. Then "Preparing your
desktop..." and you're in. Run it again from CONTROL > Run Setup Again... or
`python3 neptune.py --oobe`. Installs from before Setup existed skip it; the
v2.4 upgrade package turns it on for upgraded machines.
