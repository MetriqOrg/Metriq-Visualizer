#!/usr/bin/env bash
set -euo pipefail

: "${QT_ANDROID_ROOT:?Set QT_ANDROID_ROOT to Qt's android_arm64_v8a directory}"
: "${QT_HOST_PATH:?Set QT_HOST_PATH to the matching desktop Qt directory}"
: "${ANDROID_SDK_ROOT:?Set ANDROID_SDK_ROOT}"
: "${ANDROID_NDK_ROOT:?Set ANDROID_NDK_ROOT}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="${METRIQ_ANDROID_BUILD_DIR:-${root}/mobile/build-android-arm64}"

cmake -S "${root}/mobile" -B "${build_dir}" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DBUILD_TESTING=OFF \
  -DCMAKE_TOOLCHAIN_FILE="${QT_ANDROID_ROOT}/lib/cmake/Qt6/qt.toolchain.cmake" \
  -DQT_HOST_PATH="${QT_HOST_PATH}" \
  -DANDROID_SDK_ROOT="${ANDROID_SDK_ROOT}" \
  -DANDROID_NDK="${ANDROID_NDK_ROOT}" \
  -DANDROID_ABI=arm64-v8a

cmake --build "${build_dir}" --target apk --parallel 2
find "${build_dir}" -type f \( -name '*.apk' -o -name '*.aab' \) -print
