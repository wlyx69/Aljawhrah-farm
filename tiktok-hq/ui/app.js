/* TikTok HQ - page logic. Talks to Python through window.pywebview.api (see tiktok_hq_ui.py). */
(function () {
  'use strict';

  // ------------------------------------------------------------------ i18n
  var STR = {
    ar: {
      title: 'جهّز الفيديو عشان تيك توك يعرضه بجودته الأصلية',
      subtitle: 'بدون إعادة ضغط. الصورة والصوت يوصلون للمشاهد مثل ما هم.',
      drop_title: 'اسحب الفيديو هنا', drop_or: 'أو', drop_pick: 'اختر ملف',
      drop_hint: 'MP4 / MOV / MKV / WebM … حتى 4 GB و10 دقايق',
      options: 'الإعدادات', advanced: 'خيارات متقدمة', method: 'الطريقة',
      m_audio: 'الافتراضية', m_audio_s: 'audio · موصى بها',
      m_ghost: 'تضخيم الفيديو', m_ghost_s: 'ghost · مكشوفة',
      m_elst: 'elst', m_elst_s: 'تجريبية', m_fps: 'fps', m_fps_s: 'تجريبية',
      m_all: 'كل الطرق', m_all_s: 'ملف لكل طريقة',
      ghost_warn: 'تيك توك يكشف هذي الطريقة من أغسطس 2026 (المنشور يصير خاص). للتجربة بس.',
      tags: 'التوقيع داخل الملف', t_replica: 'برنامج مونتاج', t_plain: 'ffmpeg قديم',
      force: 'إعادة ضغط إجبارية (بس إذا تيك توك أعاد ضغط ملفك)',
      outdir: 'مجلد الناتج', outdir_ph: 'فاضي = بجانب الفيديو الأصلي', choose: 'اختر',
      prepare: 'جهّز الفيديو', check: 'فحص الملف', compare: 'قارن المرفوع بالمعروض',
      s1: 'تجهيز', s2: 'تعديل الحاوية', s3: 'تحقق',
      done_title: 'تم. الملف جاهز للرفع', open_folder: 'افتح المجلد',
      n1: 'ارفعه من متصفح الكمبيوتر (tiktok.com/upload أو TikTok Studio)، مو من تطبيق الجوال.',
      n2: 'خلّ خيار الرفع بجودة عالية (HD) مفعّل.',
      n3: 'لا تسوي أي تعديل أو قص أو صوت داخل تيك توك.',
      n4: 'انشره أول شي بخصوصية (Only you) وانتظر 5 دقايق.',
      n5: 'تأكد من الجودة من جهاز أو حساب ثاني، أو من بوت الفحص، ثم خلّه للكل.',
      err_title: 'ما اكتمل', log: 'السجل التفصيلي',
      foot: 'يشتغل على جهازك بالكامل. ما يطلع شي للإنترنت.',
      // dynamic
      plan_copy: 'بدون إعادة ترميز: الصورة تنسخ مثل ما هي',
      plan_encode: 'بيعاد ترميز الفيديو (مرة وحدة بجودة عالية)',
      plan_audio_copy: 'الصوت مثل ما هو', plan_audio_encode: 'الصوت يتحول AAC', plan_audio_silent: 'بيضاف صوت صامت',
      already_output: 'هذا ملف جاهز من قبل', hdr: 'HDR → SDR', no_audio: 'بدون صوت', error_generic: 'خطأ',
      busy: 'فيه عملية شغالة الحين', no_file: 'اختر فيديو أول',
      probing: 'جاري قراءة الملف…', working: 'جاري التجهيز…', checking: 'جاري الفحص…', comparing: 'جاري المقارنة…',
      pick_served: 'اختر الملف اللي نزّلته من تيك توك (المعروض)', pick_uploaded: 'الحين اختر الملف اللي رفعته (ناتج TikTok HQ)',
      drop_nopath: 'ما قدرت أقرأ مسار الملف المسحوب. اضغط "اختر ملف".',
      tools_missing: 'ffmpeg غير موجود داخل البرنامج',
      bridge_error: 'صار خطأ داخلي في البرنامج',
      compare_failed: 'ما تمت المقارنة. تأكد إن الملفين فيديو وموجودين.',
      v_IDENTICAL: 'تيك توك عرض ملفك بايت ببايت. هذا المستوى الأصلي.',
      'v_SAME MEDIA': 'الحاوية تغيّرت لكن الصورة والصوت نفسهم بالضبط (جودة أصلية).',
      'v_SAME PICTURE': 'الصورة مطابقة في أول ثواني (جودة أصلية، أعيد تغليفها).',
      'v_RE-ENCODED': 'تيك توك أعاد ضغط هذا الرفع. جرّب توقيع plain أو كل الطرق وارفعها كـ Only you.',
      check_done: 'انتهى الفحص. التفاصيل في السجل.',
      // engine reasons (why the video is re-encoded) and warnings, {0} {1} = values
      r_codec: 'الكودك {0} يتحول إلى H.264', r_pixfmt: 'صيغة الألوان {0} تتحول إلى 8-bit',
      r_res_high: 'الدقة {0}×{1} تنزل إلى 1080p', r_fps_high: '{0} إطار بالثانية يتحول إلى 60',
      r_interlaced: 'فيديو متداخل (interlaced) يتحول إلى عادي', r_profile: 'بروفايل H.264 غير معتاد ({0})',
      r_forced: 'إعادة ضغط إجبارية', r_no_video: 'الملف ما فيه صورة فيديو',
      w_no_audio: 'ما فيه صوت: بنضيف مسار صامت (الطريقة تحتاجه)', w_over_4gb: 'الملف أكبر من حد تيك توك (4 GB)',
      w_fps_low: '{0} إطار بالثانية أقل من الحد الأدنى عند تيك توك (23)', w_res_low: 'الدقة {0}×{1} أقل من الحد الأدنى (360 بكسل)',
      w_letterbox: 'المقاس {0}×{1} مو 9:16، تيك توك بيضيف حواف (ما تأثر على الجودة)',
      w_level: 'مستوى H.264 {0} أعلى من 4.2', w_vfr: 'معدل الإطارات متغير (شائع في تسجيل الشاشة). إذا تيك توك أعاد ضغطه فعّل إعادة الضغط الإجبارية',
    },
    en: {
      title: 'Prepare a video so TikTok serves it at its original quality',
      subtitle: 'No re-compression. The picture and sound reach viewers as they are.',
      drop_title: 'Drop the video here', drop_or: 'or', drop_pick: 'choose a file',
      drop_hint: 'MP4 / MOV / MKV / WebM … up to 4 GB and 10 minutes',
      options: 'Options', advanced: 'Advanced', method: 'Method',
      m_audio: 'Default', m_audio_s: 'audio · recommended',
      m_ghost: 'Video inflation', m_ghost_s: 'ghost · detected',
      m_elst: 'elst', m_elst_s: 'experimental', m_fps: 'fps', m_fps_s: 'experimental',
      m_all: 'All methods', m_all_s: 'one file per method',
      ghost_warn: 'TikTok has been detecting this method since Aug 2026 (posts forced private). Experiments only.',
      tags: 'Signature inside the file', t_replica: 'Video editor', t_plain: 'old ffmpeg',
      force: 'Force re-encode (only if TikTok re-compressed your file)',
      outdir: 'Output folder', outdir_ph: 'empty = next to the source video', choose: 'Choose',
      prepare: 'Prepare video', check: 'Check file', compare: 'Compare served vs uploaded',
      s1: 'Stage', s2: 'Patch container', s3: 'Verify',
      done_title: 'Done. The file is ready to upload', open_folder: 'Open folder',
      n1: 'Upload from a PC browser (tiktok.com/upload or TikTok Studio), not the phone app.',
      n2: 'Keep the high-quality (HD) upload option on.',
      n3: 'Make no edits, trims or sounds inside TikTok.',
      n4: 'Post as "Only you" first and wait 5 minutes.',
      n5: 'Judge the quality from another device or account, or with the checker bot, then make it public.',
      err_title: 'Not completed', log: 'Detailed log',
      foot: 'Runs entirely on your computer. Nothing is sent to the internet.',
      plan_copy: 'No re-encode: the picture is copied as is',
      plan_encode: 'The video will be re-encoded (once, high quality)',
      plan_audio_copy: 'Audio kept as is', plan_audio_encode: 'Audio converted to AAC', plan_audio_silent: 'A silent track will be added',
      already_output: 'already a prepared file', hdr: 'HDR → SDR', no_audio: 'no audio', error_generic: 'error',
      busy: 'A job is already running', no_file: 'Choose a video first',
      probing: 'Reading the file…', working: 'Preparing…', checking: 'Checking…', comparing: 'Comparing…',
      pick_served: 'Choose the file you downloaded from TikTok (served)', pick_uploaded: 'Now choose the file you uploaded (TikTok HQ output)',
      drop_nopath: 'Could not read the dropped file path. Use "choose a file".',
      tools_missing: 'ffmpeg is missing inside the app',
      bridge_error: 'Internal error in the app',
      compare_failed: 'The comparison did not run. Make sure both files are videos and still exist.',
      v_IDENTICAL: 'TikTok served your file byte for byte. This is the original tier.',
      'v_SAME MEDIA': 'The container was rewritten but the video/audio bytes are identical (original quality).',
      'v_SAME PICTURE': 'Decoded frames are identical for the first seconds (original quality, re-muxed).',
      'v_RE-ENCODED': 'TikTok re-compressed this upload. Try the plain signature or all methods, posted as Only you.',
      check_done: 'Check finished. Details are in the log.',
      r_codec: '{0} codec becomes H.264', r_pixfmt: 'pixel format {0} becomes 8-bit',
      r_res_high: '{0}×{1} is scaled to 1080p', r_fps_high: '{0} fps becomes 60',
      r_interlaced: 'interlaced video is de-interlaced', r_profile: 'unusual H.264 profile ({0})',
      r_forced: 'forced re-encode', r_no_video: 'the file has no video picture',
      w_no_audio: 'no audio: a silent track will be added (the method needs one)', w_over_4gb: 'larger than TikTok\'s 4 GB limit',
      w_fps_low: '{0} fps is below TikTok\'s minimum (23)', w_res_low: '{0}×{1} is below the minimum (360 px)',
      w_letterbox: '{0}×{1} is not 9:16, TikTok adds borders (no quality impact)',
      w_level: 'H.264 level {0} is above 4.2', w_vfr: 'variable frame rate (screen recordings often are). If TikTok re-encodes it, use force re-encode',
    }
  };

  var state = { lang: 'ar', theme: 'dark', file: null, busy: false, what: '', outDir: null, prefs: {},
    pollTimer: null, polling: false, logOpen: false };

  function $(id) { return document.getElementById(id); }
  function t(key) { var d = STR[state.lang] || STR.ar; return d[key] !== undefined ? d[key] : (STR.ar[key] !== undefined ? STR.ar[key] : key); }
  function has(key) { return (STR[state.lang] || STR.ar)[key] !== undefined; }
  function fmt(s, args) { return String(s).replace(/\{(\d+)\}/g, function (m, i) { return args && args[i] !== undefined ? args[i] : m; }); }
  function show(id, on) { $(id).classList.toggle('hidden', !on); }

  function applyLang(lang) {
    state.lang = STR[lang] ? lang : 'ar';
    var root = document.documentElement;
    root.lang = state.lang;
    root.dir = state.lang === 'ar' ? 'rtl' : 'ltr';
    var nodes = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = t(nodes[i].getAttribute('data-i18n'));
    var phs = document.querySelectorAll('[data-i18n-ph]');
    for (var j = 0; j < phs.length; j++) phs[j].setAttribute('placeholder', t(phs[j].getAttribute('data-i18n-ph')));
    var arias = document.querySelectorAll('[data-i18n-aria]');
    for (var k = 0; k < arias.length; k++) arias[k].setAttribute('aria-label', t(arias[k].getAttribute('data-i18n-aria')));
    $('lang-label').textContent = state.lang === 'ar' ? 'EN' : 'ع';
    if (state.file) renderFile(state.file);
  }
  function applyTheme(theme) {
    state.theme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
  }
  function savePrefs() {
    state.prefs.lang = state.lang; state.prefs.theme = state.theme; state.prefs.outDir = $('outdir').value || '';
    api().set_prefs(state.prefs).then(null, function () {});
  }

  // --------------------------------------------------------------- bridge
  function api() { return window.pywebview.api; }
  function errText(e) { return (e && (e.message || e.name)) ? String(e.message || e.name) : String(e || t('bridge_error')); }
  function toast(msg, ms) {
    var el = $('toast'); el.textContent = msg; el.classList.remove('hidden');
    clearTimeout(toast._t); toast._t = setTimeout(function () { el.classList.add('hidden'); }, ms || 3200);
  }
  function appendLog(text) {
    var log = $('log');
    log.textContent += text + '\n';
    if (log.textContent.length > 400000) log.textContent = log.textContent.slice(-300000);
    log.scrollTop = log.scrollHeight;
  }
  function clearLog() { $('log').textContent = ''; }
  function openLog() { state.logOpen = true; show('log', true); $('log-chev').classList.add('open'); }
  function showError(message) { $('error-text').textContent = message || ''; show('error', true); openLog(); }

  // -------------------------------------------------------------- file card
  function fmtDur(s) { s = Math.round(s || 0); var m = Math.floor(s / 60); return m + ':' + (s % 60 < 10 ? '0' : '') + (s % 60); }
  function codedText(prefix, item) {
    if (item && item.code && has(prefix + item.code)) return fmt(t(prefix + item.code), item.args);
    return item ? item.text : '';
  }
  function renderFile(info) {
    $('f-name').textContent = info.name;
    var specs = [info.width + '×' + info.height, (info.fps ? info.fps.toFixed(2).replace(/\.?0+$/, '') + ' fps' : ''), (info.vcodec || '').toUpperCase(),
      info.size_h, fmtDur(info.duration), info.acodec ? info.acodec.toUpperCase() : t('no_audio')].filter(Boolean);
    $('f-specs').textContent = specs.join('  ·  ');
    var plan = $('f-plan'); plan.innerHTML = '';
    function chip(cls, text) { var c = document.createElement('span'); c.className = 'chip ' + cls; c.dir = 'auto'; c.textContent = text; plan.appendChild(c); }
    var reasons = info.reasons_coded || (info.reasons || []).map(function (r) { return { code: null, args: [], text: r }; });
    var warnings = info.warnings_coded || (info.warnings || []).map(function (w) { return { code: null, args: [], text: w }; });
    if (info.plan_video === 'copy') chip('ok', t('plan_copy'));
    else if (info.plan_video === 'encode') chip('info', t('plan_encode'));
    else chip('warn', reasons.length ? codedText('r_', reasons[0]) : t('error_generic'));
    if (info.plan_audio === 'copy') chip('ok', t('plan_audio_copy'));
    else if (info.plan_audio === 'encode') chip('info', t('plan_audio_encode'));
    else if (info.plan_audio === 'silent') chip('info', t('plan_audio_silent'));
    if (info.plan_video === 'encode') {
      reasons.forEach(function (r) { if (r.code !== 'hdr' && r.code !== 'audio') chip('info', codedText('r_', r)); });
    }
    if (info.hdr) chip('info', t('hdr'));
    if (info.already_output) chip('warn', t('already_output'));
    warnings.forEach(function (w) { if (w.code !== 'no_audio' || info.plan_audio !== 'silent') chip('warn', codedText('w_', w)); });
    show('drop-empty', false); show('filecard', true);
    $('drop').classList.add('has-file');
    $('btn-prepare').disabled = state.busy; $('btn-check').disabled = state.busy;
  }
  function clearFile() {
    if (state.busy) { toast(t('busy')); return; }
    state.file = null;
    show('filecard', false); show('drop-empty', true);
    $('drop').classList.remove('has-file');
    $('btn-prepare').disabled = true; $('btn-check').disabled = true;
    hideResults();
  }
  function setFile(path) {
    if (!path) return;
    if (state.busy) { toast(t('busy')); return; }
    hideResults();
    toast(t('probing'), 1500);
    api().probe(path).then(function (info) {
      if (state.busy) return;
      if (!info || !info.ok) { toast((info && info.error) || t('error_generic'), 6000); return; }
      state.file = info; renderFile(info);
    }, function (e) { toast(t('bridge_error') + ': ' + errText(e), 6000); });
  }
  function hideResults() { show('result', false); show('error', false); show('verdict', false); show('progress', false); }

  // --------------------------------------------------------------- options
  function segValue(id) { var a = $(id).querySelector('.seg.active'); return a ? a.getAttribute('data-value') : null; }
  function setSeg(id, value) {
    var all = $(id).querySelectorAll('.seg');
    for (var i = 0; i < all.length; i++) {
      var on = all[i].getAttribute('data-value') === value;
      all[i].classList.toggle('active', on);
      all[i].setAttribute('aria-checked', on ? 'true' : 'false');
    }
  }
  function wireSegmented(id, onChange) {
    $(id).addEventListener('click', function (e) {
      var b = e.target.closest('.seg'); if (!b) return;
      var v = b.getAttribute('data-value'); setSeg(id, v);
      if (onChange) onChange(v);
    });
  }

  // ----------------------------------------------------------------- jobs
  function setBusy(on, what) {
    state.busy = on; state.what = what || '';
    document.body.classList.toggle('busy', on);
    $('btn-prepare').disabled = on || !state.file;
    $('btn-check').disabled = on || !state.file;
    $('btn-compare').disabled = on;
    $('btn-pick').disabled = on; $('btn-clear').disabled = on;
    if (!state.polling) schedulePoll(on ? 120 : 500);
  }
  function resetProgress() {
    var steps = $('progress').querySelectorAll('.step');
    for (var i = 0; i < steps.length; i++) steps[i].classList.remove('active', 'done');
    $('bar-fill').style.width = '0%'; $('bar-fill').classList.add('indeterminate'); $('status').textContent = '';
  }
  function setStage(n) {
    var steps = $('progress').querySelectorAll('.step');
    for (var i = 0; i < steps.length; i++) {
      var k = i + 1; steps[i].classList.toggle('done', k < n); steps[i].classList.toggle('active', k === n);
    }
    if (n > 1) { $('bar-fill').classList.add('indeterminate'); }
  }
  function startJob(what, promise) {
    hideResults(); clearLog(); $('error-text').textContent = '';
    setBusy(true, what);
    if (what === 'prep') { show('progress', true); resetProgress(); setStage(1); }
    toast(what === 'prep' ? t('working') : what === 'check' ? t('checking') : t('comparing'), 1500);
    promise.then(function (r) {
      if (!r || !r.started) {
        setBusy(false); show('progress', false);
        if (r && r.error === 'busy') toast(t('busy')); else showError((r && r.error) || t('bridge_error'));
      }
    }, function (e) { setBusy(false); show('progress', false); showError(t('bridge_error') + ': ' + errText(e)); });
  }
  function onPrepare() {
    if (state.busy) { toast(t('busy')); return; }
    if (!state.file) { toast(t('no_file')); return; }
    var opts = { method: segValue('method') || 'audio', tags: segValue('tags') || 'replica', force_encode: $('force').checked, out_dir: $('outdir').value.trim() || null };
    savePrefs();
    startJob('prep', api().prepare(state.file.path, opts));
  }
  function onCheck() {
    if (state.busy) { toast(t('busy')); return; }
    if (!state.file) { toast(t('no_file')); return; }
    openLog();
    startJob('check', api().check(state.file.path));
  }
  function onCompare() {
    if (state.busy) { toast(t('busy')); return; }
    toast(t('pick_served'), 4000);
    api().pick_file().then(function (served) {
      if (!served) return;
      toast(t('pick_uploaded'), 4000);
      api().pick_file().then(function (uploaded) {
        if (!uploaded || state.busy) return;
        openLog();
        startJob('compare', api().compare(served, uploaded));
      }, function (e) { toast(t('bridge_error') + ': ' + errText(e), 6000); });
    }, function (e) { toast(t('bridge_error') + ': ' + errText(e), 6000); });
  }

  // ----------------------------------------------------------------- events
  function handle(ev) {
    switch (ev.kind) {
      case 'line': appendLog(ev.text); break;
      case 'stage': if (state.busy) setStage(ev.stage); break;
      case 'progress':
        if (!state.busy) break;
        if (ev.pct === null || ev.pct === undefined) {
          if (ev.text) { $('bar-fill').classList.add('indeterminate'); } else { $('bar-fill').classList.remove('indeterminate'); $('bar-fill').style.width = '100%'; }
        } else { $('bar-fill').classList.remove('indeterminate'); $('bar-fill').style.width = ev.pct.toFixed(1) + '%'; }
        $('status').textContent = ev.text || '';
        break;
      case 'start': break;
      case 'error': $('error-text').textContent = ev.message || ''; break;
      case 'file': setFile(ev.path); break;
      case 'file_nopath': toast(t('drop_nopath'), 5000); break;
      case 'done': onDone(ev); break;
    }
  }
  function onDone(ev) {
    setBusy(false);
    if (ev.what === 'prep') {
      if (ev.rc === 0 && !ev.error) {
        var steps = $('progress').querySelectorAll('.step'); for (var i = 0; i < steps.length; i++) { steps[i].classList.add('done'); steps[i].classList.remove('active'); }
        $('bar-fill').classList.remove('indeterminate'); $('bar-fill').style.width = '100%'; $('status').textContent = '';
        state.outDir = ev.out_dir;
        $('outputs').textContent = (ev.outputs || []).join('\n');
        show('result', true);
      } else { show('progress', false); showError($('error-text').textContent || t('error_generic')); }
    } else if (ev.what === 'compare') {
      if (!ev.verdict) { showError($('error-text').textContent || t('compare_failed')); return; }
      $('verdict-badge').textContent = ev.verdict;
      $('verdict-badge').className = 'verdict-badge ' + (ev.verdict === 'RE-ENCODED' ? 'bad' : 'good');
      $('verdict-text').textContent = t('v_' + ev.verdict);
      show('verdict', true);
    } else if (ev.what === 'check') {
      if (ev.rc === 0 && !ev.error) toast(t('check_done'), 3000); else showError($('error-text').textContent || t('error_generic'));
    }
  }
  function poll() {
    if (!window.pywebview || !window.pywebview.api) { schedulePoll(300); return; }
    if (state.polling) return;
    state.polling = true;
    api().poll().then(function (events) {
      state.polling = false;
      (events || []).forEach(handle);
      schedulePoll(state.busy ? 120 : 500);
    }, function () { state.polling = false; schedulePoll(1000); });
  }
  function schedulePoll(ms) { clearTimeout(state.pollTimer); state.pollTimer = setTimeout(poll, ms); }

  // ------------------------------------------------------------------- init
  function insideDrop(e) { return !!(e.target && e.target.closest && e.target.closest('#drop')); }
  function wire() {
    $('btn-pick').addEventListener('click', function () {
      if (state.busy) { toast(t('busy')); return; }
      api().pick_file().then(function (p) { if (p) setFile(p); }, function (e) { toast(t('bridge_error') + ': ' + errText(e), 6000); });
    });
    $('drop').addEventListener('click', function (e) { if (!state.file && !state.busy && !e.target.closest('button')) $('btn-pick').click(); });
    $('drop').addEventListener('keydown', function (e) { if ((e.key === 'Enter' || e.key === ' ') && !state.file && e.target === $('drop')) { e.preventDefault(); $('btn-pick').click(); } });
    $('btn-clear').addEventListener('click', clearFile);
    ['dragenter', 'dragover'].forEach(function (n) { $('drop').addEventListener(n, function (e) { e.preventDefault(); $('drop').classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (n) { $('drop').addEventListener(n, function (e) { e.preventDefault(); $('drop').classList.remove('over'); }); });
    // anywhere else: no drop target (and never let the web view navigate to a dropped file)
    document.addEventListener('dragover', function (e) { e.preventDefault(); if (!insideDrop(e) && e.dataTransfer) e.dataTransfer.dropEffect = 'none'; });
    document.addEventListener('drop', function (e) { e.preventDefault(); });

    wireSegmented('method', function (v) { show('ghost-warn', v === 'ghost' || v === 'all'); });
    wireSegmented('tags');
    $('btn-advanced').addEventListener('click', function () {
      var hidden = $('advanced').classList.toggle('hidden');
      $('btn-advanced').classList.toggle('open', !hidden);
      $('btn-advanced').setAttribute('aria-expanded', hidden ? 'false' : 'true');
    });
    $('btn-outdir').addEventListener('click', function () { api().pick_dir().then(function (p) { if (p) { $('outdir').value = p; savePrefs(); } }, function () {}); });
    $('outdir').addEventListener('change', savePrefs);
    $('btn-prepare').addEventListener('click', onPrepare);
    $('btn-check').addEventListener('click', onCheck);
    $('btn-compare').addEventListener('click', onCompare);
    $('btn-open').addEventListener('click', function () { if (state.outDir) api().open_folder(state.outDir).then(null, function () {}); });
    $('btn-log').addEventListener('click', function () {
      state.logOpen = $('log').classList.toggle('hidden') === false;
      $('log-chev').classList.toggle('open', state.logOpen);
      $('btn-log').setAttribute('aria-expanded', state.logOpen ? 'true' : 'false');
    });
    $('btn-theme').addEventListener('click', function () { applyTheme(state.theme === 'dark' ? 'light' : 'dark'); savePrefs(); });
    $('btn-lang').addEventListener('click', function () { applyLang(state.lang === 'ar' ? 'en' : 'ar'); savePrefs(); });
  }

  function boot() {
    api().get_prefs().then(function (prefs) {
      state.prefs = prefs || {};
      applyTheme(state.prefs.theme || 'dark');
      applyLang(state.prefs.lang || 'ar');
      if (state.prefs.outDir) $('outdir').value = state.prefs.outDir;
    }, function () {});
    api().info().then(function (info) {
      $('version').textContent = 'v' + info.version;
      if (!info.tools_ok) { toast(t('tools_missing'), 8000); appendLog('ERROR: ' + (info.tools_error || '')); openLog(); }
      else { $('tools-line').textContent = 'ffmpeg: ' + info.ffmpeg; }
    }, function (e) { appendLog('ERROR: ' + errText(e)); openLog(); });
    schedulePoll(200);
  }

  document.addEventListener('DOMContentLoaded', function () {
    applyLang('ar'); wire();
    if (window.pywebview && window.pywebview.api) boot();
    else window.addEventListener('pywebviewready', boot);
  });
})();
