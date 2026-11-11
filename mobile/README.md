# COS Neptune Mobile

COS Neptune as a phone: the same shell three ways.

| Version | Where | Run it |
|---|---|---|
| Web / PWA | `web/` + `server.py` | `python3 server.py`, then open `http://<this PC>:8620/` on the phone (type the `http://`) |
| Android APK | `android/` | `bash android/build.sh` → `dist/NeptuneMobile.apk`; phones can download it from `http://<this PC>:8620/apk` |
| Python (PC) | `python/` | `python3 python/neptune_mobile.py` (`--scale 0.8` for a smaller window) |

**IBBS** works in all three. The web version goes through `server.py`'s relay; the APK and the
Python version talk to the board themselves. If the board server (default `192.168.1.115:8619`)
is on this PC and isn't running, `server.py` and the Python version use the local board files
(`~/.tcf_ibbs`) instead.

**Python version:** built on Neptune's own `nep/` toolkit (glass, wallpapers, icons), drawn with
PIL and shown with pygame. The mouse is your finger: click = tap, drag = swipe, hold = long-press,
right-click or Esc = Back, F5 = screenshot to Pictures, Ctrl+Q = quit. Files and settings live in
`python/home/`. `--shot out.png --steps steps.json` renders without a window, for testing.

**APK toolchain:** `android/setup-toolchain.sh` unpacks the JDK and Android SDK that live in
`~/.local/share/neptune-mobile-build/` and builds. Keep `neptune-debug.keystore` there: Android
only installs updates signed with the same key.
