# الأدلة خلف TikTok HQ

تاريخ البحث: 6 أكتوبر 2026. كل الروابط عامة. التصنيف: **مؤكد** (قرأت الكود/الملف بنفسي) / **منقول** (قاله مصدر خارجي مع دليل) / **استنتاج** / **غير مؤكد**.

## 1. وش معنى "Original" في تيك توك؟

**منقول بدليل.** صورة إثبات من بوت `re:TikTok Checker & Downloader` (Telegram) لفيديو مرفوع من المتصفح بتاريخ 16 يونيو 2026 تبيّن:

```
Quality: Browser 1080p60 | Phone 1080p60
play_addr: original_1080_0   1080p60 · 16.3 MBps · h264 · 48.5 MB
Original: 1920x1080
```

الملف المرفوع كان 50.8 MB (= 48.4 MiB) H.264 1080p60، والملف المقدَّم 48.5 MB h264 بنفس المعدل. يعني تيك توك قدّم الملف نفسه، ومستوى الجودة اسمه الداخلي `original_1080_0`.

المصدر: `proof.png` في مستودع [BastienGimbert/tiktok-quality](https://github.com/BastienGimbert/tiktok-quality) (MIT، آخر commit 16 يونيو 2026).

**استنتاج:** خيار "Original" اللي يظهر للمشاهد في قائمة الجودة هو هذا المستوى. ما لقيت توثيق رسمي من تيك توك له.

## 2. الطرق المتداولة وكيف تشتغل (مؤكد من الكود)

| المستودع | التاريخ | وش يعدّل | الادعاء |
|---|---|---|---|
| [BastienGimbert/tiktok-quality](https://github.com/BastienGimbert/tiktok-quality) | 2026-06-16 | جدول عينات الفيديو ×10 (`stts/stsz/stsc/stco`) بعينات وهمية 8 بايت `00 00 00 04 00 00 00 00`، حذف SEI من أول عينة، `ftyp=isom`، `moov` قبل `mdat`، إعادة تسمية handler الصوت، وسم تعليق | نسخة مطابقة بايت-ببايت لمخرجات سيرفر إضافة "TikTok Enhancer" (v2.editingnews.com). إثبات 1080p60 `original_1080_0` |
| [irgifebry/NoBlur](https://github.com/irgifebry/NoBlur) | 2026-07-12 | نفس نفخ جدول العينات ×10 (عينات صفرية)، `isom`، faststart | changelog 2.4.0 (18 يونيو 2026): "Confirmed: Frame Density Inflation alone bypasses TikTok recompression" |
| [buwryme/tiktok-lossless-upload](https://github.com/buwryme/tiktok-lossless-upload) | 2026-10-06 | ترميز H.264 CRF 18 ثم: نسخ مسار الصوت ونفخ `stsz` فيه ×10، `mvhd` v1 بمدة مجهولة، تصفير أزمنة `mdhd`، `elst`+1، صناديق meta مزدوجة، صندوق `name` غير معروف، 184100 بايت زايدة في النهاية | "tiktok's transcoders choke on the structural mismatch and skip re-encoding". عنده شارة حالة حية للطريقة |
| [MisticGG/fps-method](https://github.com/MisticGG/fps-method) | 2026-05-18 | ترميز H.265 60fps ثم كتابة `0x10000001` في version/flags لصندوق `elst` (إصدار غير صالح) | "causes TikTok's upload pipeline to skip its recompression step" |
| [ut0ku/120fps-method](https://github.com/ut0ku/120fps-method) | 2025-09-19 | قسمة timescale والمدة في `mvhd` و`mdhd` على 2 (60fps) أو 4 (120fps) | هدفه حفظ معدل الإطارات، مو بالضرورة تقديم الملف الأصلي |
| [KizaruZero/tiktok-fidelity](https://github.com/KizaruZero/tiktok-fidelity) | 2026-07-29 | تنفيذ مستقل لطريقتي النفخ والـ timescale | يصفها صراحة بأنها تجريبية |

**استنتاج قوي:** القاسم المشترك بين الطرق اللي تدّعي "بدون إعادة ضغط" هو إنها تخلي الحاوية غير قابلة للمعالجة الصحيحة من محوّل تيك توك، فيرجع يقدّم الملف الأصلي. عند فك ترميز ملف فيه إطارات وهمية بـ ffmpeg تظهر أخطاء `missing picture in access unit with size 8` و`no frame!` عند الوصول للإطارات الوهمية، وهذا غالبًا نفس اللي يصير عند تيك توك.

## 3. وش اخترت للأداة ولماذا

- **الافتراضي `ghost`:** نفخ جدول عينات الفيديو ×10 بعينات `00 00 00 04 00 00 00 00` (نفس بايتات الطريقة المثبتة)، مع إبقاء بيانات الصورة والصوت كما هي بايت-ببايت. هذا أقوى دليل متاح (إثبات يونيو 2026 + تأكيد مستقل من NoBlur).
- **`--replica`:** يضيف التفاصيل الشكلية الزايدة من المخرجات المثبتة (حذف SEI، handlers، تعليق). ما فيه دليل إنها ضرورية.
- **`elst` و`fps`:** بدائل من عائلتين ثانيتين للتجربة إذا تغيّر سلوك تيك توك.
- **الترميز عند الحاجة:** H.264 High L4.2 CRF 18 بسقف 20 Mbps، لأن الملف المثبت كان h264 بمعدل 16.3 Mbps، والتوثيق الرسمي يوصي بـ H.264.

فرق تنفيذي عن الأدوات الأصلية: الأداة تحافظ على جدول `stts` الأصلي (بدل حساب متوسط واحد) وتمدّد `ctts`/`sdtp` عند وجودهما، عشان يبقى الملف متسقًا مع مواصفة ISO 14496-12 قدر الإمكان.

## 4. الحدود الرسمية (منقول عن توثيق تيك توك)

Content Posting API – Media Transfer Guide: MP4 موصى به، H.264 موصى به، 23–60 fps، 360–4096 بكسل، حتى 4 GB. (الرابط: developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide — ما قدرت أفتحه مباشرة من بيئة البحث؛ القيم منقولة عن tiktok-fidelity/docs/research.md.)

## 5. وش ما أعرفه

- ما أقدر أرفع على تيك توك من بيئة التطوير. ما فيه اختبار فعلي على تيك توك من طرفي.
- ما أعرف إذا فيه شرط على معدل البت أو الحجم عشان يقبل تيك توك المستوى الأصلي. الملف المثبت كان 16.3 Mbps / 48.5 MB.
- ما أعرف إذا الطريقة تشتغل من تطبيق الجوال. كل المصادر تقول: ارفع من المتصفح على الكمبيوتر.
- تيك توك يقدر يسدّ هذه الثغرة بأي لحظة. مستودع buwryme يعرض شارة "patcher status" حية لهذا السبب.
