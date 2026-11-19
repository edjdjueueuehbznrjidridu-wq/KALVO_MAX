# -*- coding: utf-8 -*-
"""Best-effort Android notification bridge; no-op on desktop/test environments."""
from __future__ import annotations

import hashlib
import threading
import time

CHANNEL_ID = "kalvo_market_alerts"
_RECENT_LOCK = threading.RLock()
_RECENT = {}
_DEDUPE_SECONDS = 45
_CHANNEL_NAME = "KALVO notifications"


def is_android() -> bool:
    try:
        from kivy.utils import platform
        return platform == "android"
    except Exception:
        return False


def _java_notify(notification_id: int, title: str, body: str) -> bool:
    from jnius import autoclass

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Context = autoclass("android.content.Context")
    BuildVersion = autoclass("android.os.Build$VERSION")
    NotificationManager = autoclass("android.app.NotificationManager")
    NotificationBuilder = autoclass("android.app.Notification$Builder")
    activity = None
    try:
        activity = PythonActivity.mActivity
    except Exception:
        activity = None
    if activity is None:
        # The monitor runs in a separate Android service process, where the UI
        # Activity may not exist. Use the Service context in that process.
        PythonService = autoclass("org.kivy.android.PythonService")
        activity = PythonService.mService
    manager = activity.getSystemService(Context.NOTIFICATION_SERVICE)

    if int(BuildVersion.SDK_INT) >= 26:
        NotificationChannel = autoclass("android.app.NotificationChannel")
        channel = NotificationChannel(CHANNEL_ID, _CHANNEL_NAME, NotificationManager.IMPORTANCE_DEFAULT)
        manager.createNotificationChannel(channel)
        builder = NotificationBuilder(activity, CHANNEL_ID)
    else:
        builder = NotificationBuilder(activity)

    icon = int(activity.getApplicationInfo().icon)
    builder.setSmallIcon(icon)
    builder.setContentTitle(str(title)[:90])
    builder.setContentText(str(body)[:220])
    builder.setAutoCancel(True)
    notification = builder.build()
    manager.notify(int(notification_id), notification)
    return True


def notify(notification_id: int, title: str, body: str) -> bool:
    if not is_android():
        return False
    # De-duplicate identical notifications within this process. Cross-process
    # manager digests use persistent cooldown timestamps in the shared database.
    now = time.monotonic()
    fingerprint = hashlib.sha256(
        f"{title}|{body}".encode("utf-8", "ignore")
    ).hexdigest()[:24]
    with _RECENT_LOCK:
        last = float(_RECENT.get(fingerprint, 0.0))
        if now - last < _DEDUPE_SECONDS:
            return False
        _RECENT[fingerprint] = now
        for key, value in list(_RECENT.items()):
            if now - float(value) > _DEDUPE_SECONDS * 4:
                _RECENT.pop(key, None)
    try:
        # Stable notification ids allow the same event to update rather than spam.
        return _java_notify(notification_id, title, body)
    except Exception:
        return False



def request_permission() -> bool:
    """Request Android 13+ notification permission from a visible activity."""
    if not is_android():
        return False
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        BuildVersion = autoclass("android.os.Build$VERSION")
        PackageManager = autoclass("android.content.pm.PackageManager")
        if int(BuildVersion.SDK_INT) < 33:
            return True
        activity = PythonActivity.mActivity
        permission = "android.permission.POST_NOTIFICATIONS"
        if int(activity.checkSelfPermission(permission)) == int(PackageManager.PERMISSION_GRANTED):
            return True
        activity.requestPermissions([permission], 7314)
        return True
    except Exception:
        return False

def notification_id_for(category: str, title: str, body: str, row_id: int = 0) -> int:
    if row_id:
        return int(row_id) % 2_000_000_000 or 1
    payload = f"{category}|{title}|{body}|{int(time.time() // 30)}"
    return int(hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()[:7], 16)
