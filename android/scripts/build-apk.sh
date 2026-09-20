#!/usr/bin/env bash
# Build the release APK a friend can install: one signed file, no Play Store.
# Creates the signing keystore on first run and keeps it out of git, so every
# later build is an upgrade of the same app rather than a conflicting install.
set -euo pipefail
cd "$(dirname "$0")/.."

export JAVA_HOME="${JAVA_HOME:-$(/usr/libexec/java_home -v 17 2>/dev/null || echo /opt/homebrew/opt/openjdk@17)}"
KEYSTORE="keystore/inbox-release.jks"
PROPS="keystore.properties"

if [ ! -f "$PROPS" ]; then
    mkdir -p keystore
    PASS="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9')"
    "$JAVA_HOME/bin/keytool" -genkeypair -v \
        -keystore "$KEYSTORE" -alias inbox \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass "$PASS" -keypass "$PASS" \
        -dname "CN=Inbox, OU=Inbox clone, O=Inbox clone, L=, ST=, C=US" >/dev/null
    cat > "$PROPS" <<PROPSEOF
storeFile=$KEYSTORE
storePassword=$PASS
keyAlias=inbox
keyPassword=$PASS
PROPSEOF
    chmod 600 "$PROPS"
    echo "created $KEYSTORE (back it up: losing it means the next APK cannot upgrade this one)"
fi

./gradlew --quiet :app:assembleRelease
APK="app/build/outputs/apk/release/app-release.apk"
OUT="build/Inbox.apk"
mkdir -p build
cp "$APK" "$OUT"

echo
echo "APK: $(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")  ($(du -h "$OUT" | cut -f1))"
BUILD_TOOLS="$(ls -d "${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"/build-tools/* | sort -V | tail -1)"
"$BUILD_TOOLS/apksigner" verify --print-certs "$OUT" | sed -n '1,4p'
echo "sha256: $(shasum -a 256 "$OUT" | cut -d' ' -f1)"
