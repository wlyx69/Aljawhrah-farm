# الأدلة خلف TikTok HQ

تاريخ البحث: 6 أكتوبر 2026. كل الروابط عامة على GitHub. التصنيف: **مؤكد** (قرأت الكود/الملف بنفسي) / **منقول** (قاله مصدر خارجي مع دليل) / **استنتاج** / **غير مؤكد**.

ملاحظة على المنهج: مواقع المدونات والمنتديات وReddit كانت محجوبة من بيئة البحث، فالأدلة كلها من مستودعات GitHub ومشاكلها (issues) وتواريخ الـ commits، ومن كود yt-dlp. هذا يعني إن التقارير المستخدمة من مطوّرين ومستخدمي أدوات، مو من عموم المستخدمين.

## 1. وش معنى "Original" في تيك توك؟

**منقول بدليل.** داخل API تيك توك، كل فيديو له قائمة مستويات (`bit_rate[]` / `bitrateInfo[]`) لكل واحد اسم `gear_name`. من 2023 ظهر مستوى اسمه `original_1080_0` بـ `quality_type = 10000` (yt-dlp issue #7109، مايو 2023). وفي 16 يونيو 2026 صورة من بوت `re:TikTok Checker & Downloader` لفيديو مرفوع من المتصفح تبيّن:

```
play_addr: original_1080_0   1080p60 · 16.3 MBps · h264 · 48.5 MB
```

والملف المرفوع كان 50.8 MB (= 48.4 MiB) H.264 1080p60. يعني المستوى هو الملف المرفوع نفسه. المصدر: `proof.png` في [BastienGimbert/tiktok-quality](https://github.com/BastienGimbert/tiktok-quality).

**مهم (مؤكد بالبحث):** ما لقيت أي مصدر في 2024–2026 يصف خيار "Original" في قائمة الجودة عند المشاهد. الكلمة موجودة فقط في الـ API وفي بوتات الفحص وأدوات التحميل. التحقق لازم يكون عبر بوت فحص أو تحميل الملف المقدَّم ومقارنته.

## 2. التسلسل الزمني للطرق (مؤكد من الكود والتواريخ)

| الفترة | الطريقة | الدليل | الحالة |
|---|---|---|---|
| 2024 – مارس 2025 | `ffmpeg -itsscale 2 -c copy` (تبطيء زمني) | paschafps، LuisAlves10 | LuisAlves10 يؤرّخ موتها بـ 14 مارس 2025 |
| 2025 | قسمة timescale في `mvhd`/`mdhd` على 2 أو 4 | ut0ku/120fps-method (سبتمبر 2025)، glitchfl | هدفها حفظ 60fps؛ issue مايو 2026 يشتكي من تقطيع على الجوال |
| مايو – يونيو 2026 | نفخ جدول عينات **الفيديو** ×10 (إطارات وهمية) | BastienGimbert (نسخة بايت-ببايت من سيرفر "TikTok Enhancer"، إثبات 16 يونيو)، NoBlur ("Confirmed" 18 يونيو) | **مكشوفة من أغسطس 2026** (انظر §3) |
| أغسطس – أكتوبر 2026 | نفخ جدول عينات **الصوت** في مسار مستنسخ + حذف `btrt` + مسح التوقيعات | youpzdev/tiktok-prep (29 أغسطس)، buwryme/tiktok-lossless-upload ("works as of sep 2026")، TheziessMethod (5 سبتمبر)، krutkrutaya "CompressBase" (6 أكتوبر) | آخر طريقة مُبلّغ عنها شغّالة |

## 3. كشف تيك توك لطريقة الإطارات الوهمية (منقول، 4 تقارير مستقلة)

- [NoBlur issue #2](https://github.com/irgifebry/NoBlur/issues/2) (5 أغسطس 2026): "TikTok hides patched videos with this method, even if you try to set it to public, it refuses to... it'll say the video is 'Processing'".
- [NoBlur issue #3](https://github.com/irgifebry/NoBlur/issues/3) (10 أغسطس): "the vids were all taken down and new ones can't be posted to public".
- [NoBlur issue #4](https://github.com/irgifebry/NoBlur/issues/4) (15 أغسطس) و[#5](https://github.com/irgifebry/NoBlur/issues/5) (25 أغسطس): نفس الشي، بدون رد من المطوّر.
- [BastienGimbert issue #1](https://github.com/BastienGimbert/tiktok-quality/issues/1) (23 سبتمبر): الملف عالق "under review" أكثر من 48 ساعة.
- [youpzdev README](https://github.com/youpzdev/tiktok-prep/blob/main/README.en.md) (اختبار 29 أغسطس على ملف نظيف بدون أي توقيعات): "TikTok recognises such a container and forces the video to 'only me' privacy... it was the inflation, not the tags".

لهذا السبب طريقة `ghost` في الأداة موجودة للتجارب فقط ومعها تحذير.

## 4. الطريقة الافتراضية في الأداة: مسار الصوت الوهمي (مؤكد من الكود)

مأخوذة من [youpzdev/tiktok-prep](https://github.com/youpzdev/tiktok-prep) (`mp4mask.py`)، اللي هندس عكسيًا مخرجات "patcher شغّال" بتاريخ 29 أغسطس 2026، وتتقاطع مع buwryme وTheziessMethod وkrutkrutaya:

1. **حذف SEI الخاص بـ x264** من أول إطار (سلسلة إعدادات الترميز). بيانات الصورة ما تتغير.
2. **حذف صندوق `btrt`** من كل sample entry. youpzdev: "Without this the rest does nothing... it reads the real 16 Mbps straight from the container".
3. **compressorname** في `stsd` = `EditingVC1-v1.6.0.3-cv`، و**وسم `©too`** = JSON بنفس شكل اللي يكتبه محرر تيك توك (`te_is_reencode`, `maxrate`...). خيار `--tags plain` يستبدلها بـ `Lavf59.27.100` (اللي تستخدمه buwryme وkrutkrutaya).
4. **نسخة من مسار الصوت** تُضاف كمسار ثالث: العينات الحقيقية + 9 أضعافها عينات وهمية (8 بايت، مدة tick واحد لكل وحدة، كلها في chunk واحد في نهاية الملف داخل `mdat` ثاني). `track_ID` جديد، `next_track_ID` في `mvhd` يزيد، تُحذف `edts` من النسخة، وتُحدَّث مدة `mdhd`.

الآلية (استنتاج youpzdev، قابل للتصديق تقنيًا): مقدّر تيك توك يحسب مدة الصوت = عدد العينات × 1024 ÷ تردد العينة، فيشوف 172 ثانية بدل 17، ويحسب معدل بت أقل بعشر مرات، فيقرر إن الملف ما يحتاج إعادة ضغط. المشغلات تستخدم أول مسار صوت وتتجاهل النسخة.

فرق تنفيذي عن youpzdev: الأداة تحط بيانات العينات الوهمية داخل صندوق `mdat` ثاني (ملف صالح بالمواصفة) بدل بايتات عارية بعد آخر صندوق.

**التقارير المضادة:** [buwryme issue #1](https://github.com/buwryme/tiktok-lossless-upload/issues/1) (11 أغسطس 2026): "didnt work... still had bad quality" (مغلق بدون رد). وyoupzdev نفسه: "The trick works today, but TikTok keeps tuning its algorithms, and nobody's promising it'll last".

## 5. إعدادات الترميز (منقول)

youpzdev قارن MediaInfo لملف **حمّله من تيك توك** (قدّمه تيك توك كـ original) بتاريخ 29 أغسطس 2026: `1080×1200@60 CFR, AVC High L4.2, refs 4, CABAC, 16.1 Mbps بسقف 31,948,000, BT.709, AAC-LC CBR ~200 kbps 48 kHz, Encoded_Library "EditingVC1-v1.5.0.2"`. من هنا: 0.207 بت/بكسل، سقف 32 Mbps، AAC 48 kHz 200k، High L4.2.

ملف يونيو 2026 المثبت (BastienGimbert) كان h264 1080p60 بـ 16.3 Mbps. NoBlur استخدم في نسخة قديمة CBR 14261k Main L4.2 ثم حذفه لأنه "غير ضروري".

## 6. التوقيعات اللي تتجنبها الأداة (منقول)

krutkrutaya (6 أكتوبر 2026) يقول إن تيك توك "على الأرجح" حظر وسم التعليق الثابت `TK8vY5VqBA6hUlo1yuGvNA` اللي كانت تكتبه أداة BastienGimbert/TikTok Enhancer، وإن الملفات المتطابقة بايت-ببايت وعناوين الـ chunks المتكررة وأحجام العينات المتماثلة علامات واضحة. الأداة ما تكتب أي تعليق ثابت، والـ `©too` فيه معرّف عشوائي ووقت الإنشاء.

## 7. الحدود الرسمية (منقول عن توثيق تيك توك)

Content Posting API – Media Transfer Guide: MP4 موصى به، H.264 موصى به، 23–60 fps، 360–4096 بكسل، حتى 4 GB. (developers.tiktok.com محجوب من بيئة البحث؛ القيم منقولة عن tiktok-fidelity وTheziessMethod اللي يقتبسونه.) خيار "Allow high-quality uploads" الرسمي يرفع مستوى إعادة الضغط فقط ولا يلغيه (إجماع المصادر).

## 8. وش ما أعرفه

- ما أقدر أرفع على تيك توك من بيئة التطوير. ما فيه اختبار فعلي من طرفي.
- ما أعرف إذا فيه شرط على الحجم أو المدة عشان يقبل تيك توك المستوى الأصلي. المثبت: 48.5 MB / 24 ثانية (يونيو) و16.1 Mbps (أغسطس).
- ما أعرف إذا توقيع المحرر (`replica`) أفضل من التوقيع العادي (`plain`) على المدى الطويل. كلاهما من أدوات تبلّغ إنها شغّالة؛ لذلك الخيارين موجودين.
- تيك توك يقدر يسدّ هذه الثغرة بأي لحظة.
