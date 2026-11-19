# معماری KALVO 7.x — AI-Native Market Intelligence

## اصل معماری

همهٔ اجزا از قراردادهای مشترک عبور می‌کنند: **دادهٔ معتبر → تحلیل → تصمیم → Risk Governor → اجرای شبیه‌سازی → Position Manager → Audit/Notification → یادگیری توصیفی**.

هیچ ماژولی مجاز نیست بدون اعتبارسنجی منبع و زمان، قیمت بسازد یا یک موقعیت شبیه‌سازی‌شده را تغییر دهد.

## لایه‌ها

1. **Market Data:** آداپتورهای خواندنی، normalizer، timestamp/freshness، bid/ask، OHLCV و source consistency.
2. **Analytics:** تحلیل کندل‌های بسته‌شده، رژیم بازار، نوسان، حجم و هم‌گرایی تایم‌فریم.
3. **AI Context & Reasoning:** دادهٔ نماد، خبر، تقویم، ژورنال دمو و پرسش کاربر با اعلام محدودیت و عدم‌قطعیت.
4. **Strategy Lab:** تست قواعد، walk-forward، replay و گزارش هزینه/لغزش.
5. **Risk Governor:** سقف‌های ثابت و محلی، کنترل ورودی داده، spread، ریسک، exposure و رد fail-closed.
6. **Execution Simulator:** فقط دفترکل محلی؛ خرید روی Ask، فروش روی Bid، فرض لغزش/کارمزد و رویدادهای paper fill.
7. **Position Manager:** TP/SL/trailing، pending orders، بازبینی فرضیه و قفل جلوگیری از چرخه‌های هم‌زمان.
8. **News & Calendar:** RSS محدود و allow-listed؛ BLS/FOMC/ECB calendar؛ خبر ابزار زمینهٔ ریسک است نه سیگنال مستقل.
9. **Learning Memory:** آمار توصیفی recency-weighted از معاملات بسته‌شده؛ نمونه/بازهٔ عدم‌قطعیت؛ risk-reduce only.
10. **Android Service & UI:** foreground sticky اختیاری، اعلان، تجربهٔ پاسخ‌گو و تنظیمات کاربر.

## نقش AI و محدودیت آن

AI می‌تواند سناریوها را مقایسه کند، شواهد موافق/مخالف را توضیح دهد، وضعیت NO-TRADE را برگزیند و پرسش استراتژی/نمودار را پاسخ دهد. اما:

- AI نمی‌تواند quote کهنه یا ناقص را معتبر اعلام کند.
- AI نمی‌تواند سقف Risk Governor را افزایش دهد.
- حافظهٔ معاملات گذشته، احتمال برد آینده نیست.
- AI خودآموزی عصبی یا retraining مدل پایه در این نسخه وجود ندارد؛ حافظه صرفاً آماری و قابل‌ممیزی است.
- تمام معاملات کاغذی‌اند؛ API سفارش زنده وجود ندارد.

## Background monitoring

Foreground service در پردازش جدا با `foreground:sticky` تعریف شده، به دیتابیس محلی متصل است و با قفل فایل، اجرای مدیر موقعیت در UI و service را هماهنگ می‌کند. sticky صرفاً درخواست restart است؛ Android یا سازندهٔ دستگاه می‌تواند سرویس را متوقف کند. نظارت پایدارِ مستقل از گوشی به backend سرور نیاز دارد.

## نسخهٔ جاری

7.2.0 روی 7.0.1 stability/data foundation توسعه یافته است. پوشش UI واقعی و build واقعی APK هنوز نیازمند اجرا روی GitHub Actions و دستگاه اندروید است.
