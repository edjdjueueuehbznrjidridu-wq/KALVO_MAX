[app]
title = KALVO Boot Probe
package.name = kalvobootprobe
package.domain = org.kalvo.lab
source.dir = .
source.include_exts = py,kv,png,jpg
source.exclude_dirs = bin,.git,__pycache__,.buildozer
version = 1.0.0
requirements = python3,kivy==2.2.1
orientation = portrait
fullscreen = 0
android.api = 33
android.minapi = 24
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True
android.archs = arm64-v8a
android.allow_backup = False
p4a.branch = v2024.01.21
android.release_artifact = apk
android.debug_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 0
bin_dir = ./bin
