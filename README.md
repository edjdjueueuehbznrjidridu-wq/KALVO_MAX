# KALVO 7.2.3 — Android Startup Diagnostics

**KALVO** یک محیط چندبازاری برای مشاهدهٔ داده، تحلیل زمینه‌مند، آزمایش استراتژی و مدیریت موقعیت‌های شبیه‌سازی‌شده است.

> **SEE CLEARLY · THINK DEEPLY · STAY AWARE**  
> بازار را دقیق‌تر ببین؛ تصمیم را آگاهانه‌تر بررسی کن.

## Android boot diagnostics in 7.2.3

This package also includes a separate, minimal Kivy-only Boot Probe. Run the `KALVO Android Boot Probe APK` workflow manually from GitHub Actions. Install that APK on the same phone: if it closes too, the evidence points to the shared Android/Kivy runtime path; if it stays open, the failure is more likely in KALVO-specific imports or startup code. This is a diagnostic comparison, not proof of a single root cause.

این انتشار یک بستهٔ تشخیصی برای کرش شروع اندروید است؛ رفع قطعی روی دستگاه ادعا نمی‌شود.

- ثبت breadcrumb قبل و بعد از واردکردن Kivy، موتورهای اصلی، سرویس‌ها، انتخاب فونت، آماده‌سازی پایگاه داده، ساخت داشبورد و ناوبری.
- ثبت آخرین مرحلهٔ اجرا در `user_data_dir/logs/startup-trace.log` و استثنای پایتون در `user_data_dir/logs/startup-crash.log` در فضای خصوصی برنامه.
- عقب‌انداختن اولین دریافت شبکهٔ داشبورد تا ۲٫۵ ثانیه پس از ورود به صفحه و اولین چرخهٔ مدیر موقعیت تا ۸ ثانیه، برای جلوگیری از رقابت کارهای پس‌زمینه با cold-start.
- فایل `res/drawable/presplash.jpg` که در APK دیده می‌شود، صفحهٔ موقت بومی Kivy/python-for-android با عبارت Loading است. خود تصویر علت کرش نیست؛ ماندن روی آن و خروج برنامه یعنی مرحلهٔ بعدی شروع کامل نشده است.
- اگر قبل از آغاز interpreter یا به‌علت خطای native (مانند linker/SDL/ABI) برنامه بسته شود، فایل trace پایتون ممکن است ایجاد نشود. در آن حالت Android Bug Report یا logcat لازم است.

اصلاحات نسخهٔ 7.2.1 روی دستگاه گزارش‌شده مشکل را حل نکردند؛ بنابراین این نسخه به‌جای اعلام رفع قطعی، ابزار جمع‌آوری شواهد زمان اجرا را اضافه می‌کند. تست‌های میزبان و compileall، اجرای واقعی Kivy روی گوشی را اثبات نمی‌کنند.

## Product and safety boundary

- **تمام معامله‌ها، سفارش‌ها، کیف پول و Auto-Trade در این نسخه شبیه‌سازی‌شده‌اند.** آداپتورهای بازار فقط دادهٔ عمومی خواندنی دریافت می‌کنند؛ هیچ سفارش، واریز یا برداشت زنده به صرافی ارسال نمی‌شود.
- Auto-Trade فقط در دفترکل محلی تمرینی اجرا می‌شود. نبود دادهٔ تازه/دوطرفه، خطای AI، نبود اطمینان کافی یا نقض کنترل محلی ریسک یعنی عدم ورود.
- محدودیت‌های ریسک و اهرم موجود افزایش داده نشده‌اند. حافظهٔ آماری هم مجوز افزایش بودجهٔ ریسک نمی‌دهد.
- خبر به‌تنهایی سیگنال جهت‌دار نیست. خبرخوان یک مجموعهٔ منبع محدود است؛ نبود خبر در این فیدها ثابت نمی‌کند رویدادی رخ نداده است.
- AI context و حافظهٔ آماری برای توضیح و ارزیابی هستند، نه تضمین سود یا پیش‌بینی قطعی.

## Android background monitoring

- سرویس در Buildozer با `foreground:sticky` تعریف شده و فقط بعد از روشن‌کردن گزینهٔ «نظارت پس‌زمینه» در تنظیمات شروع می‌شود.
- سرویس از همان دیتابیس محلی استفاده می‌کند، مدیر موقعیت را از طریق قفل بین‌فرایندی اجرا می‌کند و فقط رویدادهای دفترکل شبیه‌سازی را پایش می‌کند.
- Android می‌تواند سرویس را به‌دلیل مدیریت باتری، محدودیت سازنده، توقف اجباری، ری‌استارت دستگاه یا مجوزها متوقف کند. `sticky` درخواست بازیابی است، **تضمین ۲۴/۷ نیست**. Force Stop با شروع مجدد برنامه تفاوت دارد.
- سرویس برای جلوگیری از مصرف بی‌رویه، WakeLock دائمی نگه نمی‌دارد. نظارت دائمی واقعی و مستقل از گوشی به یک backend server پایدار نیاز دارد.

## Android build identity

| Setting | Value |
|---|---|
| App title | `KALVO` |
| Version | `7.2.3` |
| Package | `org.kalvo.lab.kalvomax` |
| Minimum Android API | `24` |
| Compile/target API | `33` |
| ABI | `arm64-v8a` |
| Output | Debug APK |
| Launcher icon | `assets/kalvo_icon.png` (512×512) |

The Android target is currently API 33 for the pinned python-for-android toolchain. Before a public store release, review the then-current target API and foreground-service-type requirements; Android 14+ requires a declared foreground service type for foreground services.

## Tests and local validation

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
python -m unittest discover -s tests -q
python -m compileall -q main.py apex services tests tools
python tools/validate_project.py
```

The layout breakpoint matrix is tested at 320, 360, 393, 432, 600, 768 and 1024 dp. This is not a substitute for screenshot/interaction testing on physical phones. Kivy, Android SDK/NDK and Buildozer are not installed in the local validation environment, so the real APK build and device UI pass must be confirmed by GitHub Actions and an Android device.

## Build APK with GitHub Actions

1. Extract the ZIP and place its **contents** in the root of the repository.
2. Add both `.github/workflows/build-apk.yml` and `.github/workflows/build-boot-probe.yml` to the repository at those exact paths.
3. Commit and push to `main` or `master`, or run `KALVO 7.2.3 Android APK` manually from Actions.
4. Download the direct APK artifact from the successful run.
5. For the separate compatibility check, open Actions → `KALVO Android Boot Probe APK` → Run workflow, then download its direct APK artifact.

The uploaded APK is a debug artifact, not a release-signed Play Store package.

## Keys and private configuration

No provider API key is embedded in this source package or APK. The `.env.example` file is a blank template. Enter a provider key through the app settings or a private environment configuration; never commit a real key. Any key that appeared in an earlier source bundle or public repository should be revoked and replaced at the provider.

## Known limitations

- No real exchange order-routing or live-account integration exists.
- The adaptive memory is statistical, interpretable and sample-limited; it does not train a neural model.
- AI's role in automated decisions remains subject to deterministic quote/data and risk gates; it is not an unconstrained autonomous trader.
- News and macro calendar coverage is bounded to configured public sources.
- Android service survival, notifications, Arabic typography, animations and screen layout still require real-device validation across OEMs and screen sizes.
- Full professional UI redesign across every page is an ongoing phase; the 7.2.0–7.2.3 changes add responsive behavior, visual feedback and safer Android startup but do not claim parity with a large exchange app.
