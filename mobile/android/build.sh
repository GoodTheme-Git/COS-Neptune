#!/usr/bin/env bash
# Build COS Neptune Mobile for Android without Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner.
#
#   android/build.sh            -> dist/NeptuneMobile.apk
#
# Needs a JDK 17 and the Android SDK (build-tools 34.0.0, platforms;android-34). By default
# both are looked for in ~/.local/share/neptune-mobile-build/{jdk,sdk}; override with
# JAVA_HOME and ANDROID_HOME. The APK is signed with a debug key made on first run
# (~/.local/share/neptune-mobile-build/neptune-debug.keystore); keep that file, or Android
# will refuse to install a newer build over the old one.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
MOBILE=$(dirname "$HERE")
TOOLS=${NEPTUNE_BUILD_TOOLS:-$HOME/.local/share/neptune-mobile-build}
export JAVA_HOME=${JAVA_HOME:-$TOOLS/jdk}
SDK=${ANDROID_HOME:-$TOOLS/sdk}
BT=$SDK/build-tools/34.0.0
JAR=$SDK/platforms/android-34/android.jar
KEY=$TOOLS/neptune-debug.keystore
OUT=$MOBILE/dist/NeptuneMobile.apk
B=$HERE/build
PATH=$JAVA_HOME/bin:$PATH

for f in "$JAVA_HOME/bin/javac" "$BT/aapt2" "$BT/d8" "$BT/zipalign" "$BT/apksigner" "$JAR"; do
  [ -e "$f" ] || { echo "missing $f (see the top of android/build.sh)"; exit 1; }
done

rm -rf "$B"; mkdir -p "$B"/{res,compiled,gen,classes,assets,dex} "$MOBILE/dist"

echo "== resources"
cp -r "$HERE/res/." "$B/res/"
for pair in mdpi:48 hdpi:72 xhdpi:96 xxhdpi:144 xxxhdpi:192; do
  d=${pair%%:*}; px=${pair##*:}
  mkdir -p "$B/res/mipmap-$d"
  cp "$MOBILE/web/icons/icon-$px.png" "$B/res/mipmap-$d/ic_launcher.png"
done
"$BT/aapt2" compile --dir "$B/res" -o "$B/compiled/res.zip"

echo "== assets (web/ shell)"
cp -r "$MOBILE/web" "$B/assets/web"
rm -f "$B/assets/web/sw.js"          # no service worker inside the app

echo "== link"
"$BT/aapt2" link -o "$B/unsigned.apk" -I "$JAR" --manifest "$HERE/AndroidManifest.xml" \
  --java "$B/gen" -A "$B/assets" "$B/compiled/res.zip" --auto-add-overlay

echo "== java"
find "$HERE/src" "$B/gen" -name '*.java' > "$B/sources.txt"
javac --release 8 -Xlint:-options -encoding UTF-8 -classpath "$JAR" \
  -d "$B/classes" @"$B/sources.txt"

echo "== dex"
"$BT/d8" --release --min-api 24 --lib "$JAR" --output "$B/dex" $(find "$B/classes" -name '*.class')
(cd "$B/dex" && zip -q -u "$B/unsigned.apk" classes.dex)

echo "== sign"
if [ ! -f "$KEY" ]; then
  keytool -genkeypair -keystore "$KEY" -storepass neptune -keypass neptune -alias neptune \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=COS Neptune Mobile" >/dev/null
fi
"$BT/zipalign" -f -p 4 "$B/unsigned.apk" "$B/aligned.apk"
"$BT/apksigner" sign --ks "$KEY" --ks-pass pass:neptune --key-pass pass:neptune --out "$OUT" "$B/aligned.apk"
"$BT/apksigner" verify "$OUT"
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
