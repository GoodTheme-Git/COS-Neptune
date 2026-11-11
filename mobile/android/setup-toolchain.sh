#!/usr/bin/env bash
# One-time: unpack the JDK and Android command-line tools downloaded into
# ~/.local/share/neptune-mobile-build, install build-tools 34 + platform 34
# (this accepts Google's Android SDK licence), then build the APK.
# Safe to run again: steps already done are skipped.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=${NEPTUNE_BUILD_TOOLS:-$HOME/.local/share/neptune-mobile-build}
cd "$T"

if [ ! -x jdk/bin/java ]; then
  echo "== unpacking JDK"
  rm -rf jdk jdk-17*
  tar xzf jdk.tar.gz
  mv jdk-17* jdk
fi

if [ ! -x sdk/cmdline-tools/latest/bin/sdkmanager ]; then
  echo "== unpacking Android command-line tools"
  rm -rf sdk/cmdline-tools
  mkdir -p sdk/cmdline-tools
  unzip -q cmdline.zip -d sdk/cmdline-tools
  mv sdk/cmdline-tools/cmdline-tools sdk/cmdline-tools/latest
fi

export JAVA_HOME=$T/jdk
if [ ! -x sdk/build-tools/34.0.0/aapt2 ] || [ ! -f sdk/platforms/android-34/android.jar ]; then
  echo "== installing build-tools 34.0.0 and platform android-34"
  { yes || true; } | sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root="$T/sdk" "build-tools;34.0.0" "platforms;android-34" >/dev/null
fi

echo "== toolchain ready, building"
exec "$HERE/build.sh"
