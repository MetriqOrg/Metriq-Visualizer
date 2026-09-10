#!/usr/bin/env bash
set -euo pipefail

: "${QT_IOS_ROOT:?Set QT_IOS_ROOT to the Qt iOS directory}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="${METRIQ_IOS_BUILD_DIR:-${root}/mobile/build-ios-simulator}"

cmake -S "${root}/mobile" -B "${build_dir}" -G Xcode \
  -DBUILD_TESTING=OFF \
  -DCMAKE_TOOLCHAIN_FILE="${QT_IOS_ROOT}/lib/cmake/Qt6/qt.toolchain.cmake" \
  -DCMAKE_OSX_SYSROOT=iphonesimulator \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO

cmake --build "${build_dir}" --config RelWithDebInfo --parallel 2
find "${build_dir}" -type d -name 'MetriqVisualizerMobile.app' -print
