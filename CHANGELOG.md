# Changelog

## 7.2.3 — isolated Android boot probe and stronger diagnostics

- Added a separate Kivy-only Android Boot Probe APK workflow to distinguish base Kivy/Android startup from KALVO-specific startup.
- Expanded startup breadcrumbs with PID, app-private log directory selection, Android process-log output, and optional faulthandler dump for supported fatal signals.
- Removed the embedded Coarena API credential and stopped seeding provider keys into app settings. Keys must be configured by the user.
- Corrected the source ZIP filename expected by both source-extraction stages in the APK workflow.
- This build improves diagnosis; it does not claim the device crash is already fixed.


## 7.2.2 — Android boot diagnostics and deferred startup work

- Added a persistent boot-phase trace before Kivy and application modules import, through database setup, dashboard construction, and the first app lifecycle callbacks.
- Added an early uncaught-exception report tagged with the last startup phase. Logs are written only to app-writable private locations, including the app data `logs/` folder after Kivy has initialized.
- Added checkpoints around Kivy imports, engine imports, font registration, database initialization, dashboard construction, navigation construction, and `App.run()`.
- Delayed the initial dashboard network refresh until after the first UI frame and moved the first position-supervisor cycle later to avoid competing with cold startup.
- This is a diagnostic/stability release, not a claim that the device-specific crash is already fixed. Native crashes must still be confirmed from Android Bug Report / logcat.

## 7.2.1 — Android startup stabilization

- Deferred optional SDL audio loading until after the first rendered frame; the default-off sound setting no longer initializes the audio backend during startup.
- Switched secondary Kivy screens to on-demand construction so the first launch builds only the dashboard.
- Added early uncaught-exception logging and a readable startup error view for Python exceptions raised during UI construction.
- Preserved the native Android/Kivy presplash; it is a temporary bootstrap image, not the KALVO dashboard or a cause by itself.

## 7.2.0 — Calendar, UX, Android service and AI context

- Added official BLS ICS, Federal Reserve FOMC and ECB Governing Council calendar readers with source attribution and explicit date-only/date-range precision.
- Added last-known-good calendar fallback when all calendar sources fail; cached fallback is explicitly marked stale/unverified.
- Added SEC official press-release RSS to the source allow-list and parallel news feed set.
- Expanded cloud analysis context with symbol-specific closed-paper-trade memory, news context and official calendar context.
- Tightened AI analysis instructions: do not invent chart levels, distinguish evidence from interpretation, discuss invalidation and provide NO-TRADE/DATA-INSUFFICIENT states where appropriate.
- Added responsive layout profiles and regression matrix across common phone/tablet logical widths.
- Added auto density option and adaptive dashboard metric/shortcut grids.
- Added/kept optional transitions, touch feedback, low-volume audio cues, system Noto Arabic font selection and optional Persian/Arabic text shaping.
- Added optional Android foreground sticky paper-monitor service, last-cycle status, notification permission support and service-process position-manager lock.
- Added seven reliability/UX optimizations: bounded task queue, last-good calendar cache with stale marking, parallel allow-listed news ingestion, notification dedup/cooldown, cross-process position lock, rotating crash logs, and cached Persian shaping/system-font fallback.
- Corrected the Auto-Trade risk default mismatch: Settings UI and engine now both default to 0.30%; risk/leverage caps were not raised.
- Preserved existing key configuration and previous app package identity; no private key value is printed in documentation.
- No real exchange order-routing, account access or live financial execution has been added.

## 7.0.1 — Stability & Data Integrity

- Strict two-sided quote validation for paper fills; BUY uses Ask and SELL uses Bid.
- Provider timestamp, source consistency, spread and slippage gates.
- Safer numeric validation and position-manager exit spread handling.
- Added Bybit/OKX candle and execution-simulation regression coverage.

## 7.0.0 — AI-Native Foundation

- Introduced the unified market-data, AI decision, news context, position-management and runtime architecture foundations.
- Added descriptive paper-trade memory and a structured AI decision gate.
