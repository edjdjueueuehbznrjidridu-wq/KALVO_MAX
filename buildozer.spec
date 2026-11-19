[app]
title = KALVO
package.name = kalvomax
package.domain = org.kalvo.lab
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt,wav
source.exclude_dirs = tests,bin,.git,__pycache__,.buildozer,.github,android_boot_probe
version = 7.2.3
requirements = python3,kivy==2.2.1,certifi,pyjnius,arabic-reshaper==3.0.0,python-bidi==0.4.2
icon.filename = %(source.dir)s/assets/kalvo_icon.png
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,FOREGROUND_SERVICE,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True
android.archs = arm64-v8a
android.allow_backup = False
p4a.branch = v2024.01.21
services = kalvomonitor:services/kalvo_monitor.py:foreground:sticky
android.release_artifact = apk
android.debug_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 0
bin_dir = ./bin
