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
      already_output: 'هذا ملف جاهز من قبل', hdr: 'HDR → SDR',
      busy: 'فيه عملية شغالة الحين', no_file: 'اختر فيديو أول',
      probing: 'جاري قراءة الملف…', working: 'جاري التجهيز…', checking: 'جاري الفحص…', comparing: 'جاري المقارنة…',
      pick_served: 'اختر الملف اللي نزّلته من تيك توك (المعروض)', pick_uploaded: 'الحين اختر الملف اللي رفعته (ناتج TikTok HQ)',
      drop_nopath: 'ما قدرت أقرأ مسار الملف المسحوب. اضغط "اختر ملف".',
      tools_missing: 'ffmpeg غير موجود داخل البرنامج',
      v_IDENTICAL: 'تيك توك عرض ملفك بايت ببايت. هذا المستوى الأصلي.',
      'v_SAME MEDIA': 'الحاوية تغيّرت لكن الصورة والصوت نفسهم بالضبط (جودة أصلية).',
      'v_SAME PICTURE': 'الصورة مطابقة في أول ثواني (جودة أصلية، أعيد تغليفها).',
      'v_RE-ENCODED': 'تيك توك أعاد ضغط هذا الرفع. جرّب توقيع plain أو كل الطرق وارفعها كـ Only you.',
      check_done: 'انتهى الفحص. التفاصيل في السجل.',
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
      already_output: 'already a prepared file', hdr: 'HDR → SDR',
      busy: 'A job is already running', no_file: 'Choose a video first',
      probing: 'Reading the file…', working: 'Preparing…', checking: 'Checking…', comparing: 'Comparing…',
      pick_served: 'Choose the file you downloaded from TikTok (served)', pick_uploaded: 'Now choose the file you uploaded (TikTok HQ output)',
      drop_nopath: 'Could not read the dropped file path. Use "choose a file".',
      tools_missing: 'ffmpeg is missing inside the app',
      v_IDENTICAL: 'TikTok served your file byte for byte. This is the original tier.',
      'v_SAME MEDIA': 'The container was rewritten but the video/audio bytes are identical (original quality).',
      'v_SAME PICTURE': 'Decoded frames are identical for the first seconds (original quality, re-muxed).',
      'v_RE-ENCODED': 'TikTok re-compressed this upload. Try the plain signature or all methods, posted as Only you.',
      check_done: 'Check finished. Details are in the log.',
    }
  };

  var state = { lang: 'ar', theme: 'dark', file: null, busy: false, what: '', outDir: null, prefs: {}, pollTimer: null, logOpen: false };

  function $(id) { return document.getElementById(id); }
  function t(key) { var d = STR[state.lang] || STR.ar; return d[key] !== undefined ? d[key] : (STR.ar[key] !== undefined ? STR.ar[key] : key); }
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
    $('lang-label').textContent = state.lang === 'ar' ? 'EN' : 'ع';
    if (state.file) renderFile(state.file);
  }
  function applyTheme(theme) {
    state.theme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
  }
  function savePrefs() {
    state.prefs.lang = state.lang; state.prefs.theme = state.theme; state.prefs.outDir = $('outdir').value || '';
    api().set_prefs(state.prefs);
  }

  // --------------------------------------------------------------- bridge
  function api() { return window.pywebview.api; }
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

  // -------------------------------------------------------------- file card
  function fmtDur(s) { s = Math.round(s || 0); var m = Math.floor(s / 60); return m + ':' + (s % 60 < 10 ? '0' : '') + (s % 60); }
  function renderFile(info) {
    $('f-name').textContent = info.name;
    var specs = [info.width + '×' + info.height, (info.fps ? info.fps.toFixed(2).replace(/\.?0+$/, '') + ' fps' : ''), (info.vcodec || '').toUpperCase(),
      info.size_h, fmtDur(info.duration), info.acodec ? info.acodec.toUpperCase() : 'no audio'].filter(Boolean);
    $('f-specs').textContent = specs.join('  ·  ');
    var plan = $('f-plan'); plan.innerHTML = '';
    function chip(cls, text) { var c = document.createElement('span'); c.className = 'chip ' + cls; c.textContent = text; plan.appendChild(c); }
    if (info.plan_video === 'copy') chip('ok', t('plan_copy'));
    else if (info.plan_video === 'encode') chip('info', t('plan_encode'));
    else chip('warn', (info.reasons && info.reasons[0]) || 'error');
    if (info.plan_audio === 'copy') chip('ok', t('plan_audio_copy'));
    else if (info.plan_audio === 'encode') chip('info', t('plan_audio_encode'));
    else if (info.plan_audio === 'silent') chip('info', t('plan_audio_silent'));
    if (info.hdr) chip('info', t('hdr'));
    if (info.already_output) chip('warn', t('already_output'));
    (info.warnings || []).forEach(function (w) { chip('warn', w); });
    show('drop-empty', false); show('filecard', true);
    $('drop').classList.add('has-file');
    $('btn-prepare').disabled = false; $('btn-check').disabled = false;
  }
  function clearFile() {
    state.file = null;
    show('filecard', false); show('drop-empty', true);
    $('drop').classList.remove('has-file');
    $('btn-prepare').disabled = true; $('btn-check').disabled = true;
    hideResults();
  }
  function setFile(path) {
    if (!path) return;
    hideResults();
    toast(t('probing'), 1500);
    api().probe(path).then(function (info) {
      if (!info || !info.ok) { toast((info && info.error) || 'error', 5000); return; }
      state.file = info; renderFile(info);
    });
  }
  function hideResults() { show('result', false); show('error', false); show('verdict', false); show('progress', false); }

  // --------------------------------------------------------------- options
  function segValue(id) { var a = $(id).querySelector('.seg.active'); return a ? a.getAttribute('data-value') : null; }
  function wireSegmented(id, onChange) {
    var box = $(id);
    box.addEventListener('click', function (e) {
      var b = e.target.closest('.seg'); if (!b) return;
      var all = box.querySelectorAll('.seg'); for (var i = 0; i < all.length; i++) all[i].classList.remove('active');
      b.classList.add('active'); if (onChange) onChange(b.getAttribute('data-value'));
    });
  }
  function setSeg(id, value) {
    var all = $(id).querySelectorAll('.seg');
    for (var i = 0; i < all.length; i++) all[i].classList.toggle('active', all[i].getAttribute('data-value') === value);
  }

  // ----------------------------------------------------------------- jobs
  function setBusy(on, what) {
    state.busy = on; state.what = what || '';
    document.body.classList.toggle('busy', on);
    $('btn-prepare').disabled = on || !state.file;
    $('btn-check').disabled = on || !state.file;
    $('btn-compare').disabled = on;
    $('btn-pick').disabled = on; $('btn-clear').disabled = on;
    if (on) schedulePoll(120); else schedulePoll(500);
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
    if (state.busy) { toast(t('busy')); return; }
    hideResults(); clearLog();
    setBusy(true, what);
    if (what === 'prep') { show('progress', true); resetProgress(); setStage(1); }
    toast(what === 'prep' ? t('working') : what === 'check' ? t('checking') : t('comparing'), 1500);
    promise.then(function (r) {
      if (!r || !r.started) { setBusy(false); toast((r && r.error === 'busy') ? t('busy') : 'error'); }
    });
  }
  function onPrepare() {
    if (!state.file) { toast(t('no_file')); return; }
    var opts = { method: segValue('method') || 'audio', tags: segValue('tags') || 'replica', force_encode: $('force').checked, out_dir: $('outdir').value.trim() || null };
    savePrefs();
    startJob('prep', api().prepare(state.file.path, opts));
  }
  function onCheck() {
    if (!state.file) { toast(t('no_file')); return; }
    state.logOpen = true; show('log', true); $('log-chev').classList.add('open');
    startJob('check', api().check(state.file.path));
  }
  function onCompare() {
    if (state.busy) { toast(t('busy')); return; }
    toast(t('pick_served'), 4000);
    api().pick_file().then(function (served) {
      if (!served) return;
      toast(t('pick_uploaded'), 4000);
      api().pick_file().then(function (uploaded) {
        if (!uploaded) return;
        state.logOpen = true; show('log', true); $('log-chev').classList.add('open');
        startJob('compare', api().compare(served, uploaded));
      });
    });
  }

  // ----------------------------------------------------------------- events
  function handle(ev) {
    switch (ev.kind) {
      case 'line': appendLog(ev.text); break;
      case 'stage': setStage(ev.stage); break;
      case 'progress':
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
      if (ev.rc === 0) {
        var steps = $('progress').querySelectorAll('.step'); for (var i = 0; i < steps.length; i++) { steps[i].classList.add('done'); steps[i].classList.remove('active'); }
        $('bar-fill').classList.remove('indeterminate'); $('bar-fill').style.width = '100%';
        state.outDir = ev.out_dir;
        $('outputs').textContent = (ev.outputs || []).join('\n');
        show('result', true);
      } else { show('progress', false); show('error', true); state.logOpen = true; show('log', true); $('log-chev').classList.add('open'); }
    } else if (ev.what === 'compare') {
      var v = ev.verdict || (ev.rc === 0 ? 'OK' : 'RE-ENCODED');
      $('verdict-badge').textContent = v;
      $('verdict-badge').className = 'verdict-badge ' + (v === 'RE-ENCODED' ? 'bad' : 'good');
      $('verdict-text').textContent = t('v_' + v) || '';
      show('verdict', true);
      if (ev.rc !== 0 && !ev.verdict) { show('error', true); }
    } else if (ev.what === 'check') {
      if (ev.rc === 0) toast(t('check_done'), 3000); else show('error', true);
    }
  }
  function poll() {
    if (!window.pywebview || !window.pywebview.api) { schedulePoll(300); return; }
    api().poll().then(function (events) {
      (events || []).forEach(handle);
      schedulePoll(state.busy ? 120 : 500);
    }, function () { schedulePoll(1000); });
  }
  function schedulePoll(ms) { clearTimeout(state.pollTimer); state.pollTimer = setTimeout(poll, ms); }

  // ------------------------------------------------------------------- init
  function wire() {
    $('btn-pick').addEventListener('click', function () { api().pick_file().then(function (p) { if (p) setFile(p); }); });
    $('drop').addEventListener('click', function (e) { if (!state.file && !e.target.closest('button')) $('btn-pick').click(); });
    $('drop').addEventListener('keydown', function (e) { if ((e.key === 'Enter' || e.key === ' ') && !state.file) { e.preventDefault(); $('btn-pick').click(); } });
    $('btn-clear').addEventListener('click', clearFile);
    ['dragenter', 'dragover'].forEach(function (n) { $('drop').addEventListener(n, function (e) { e.preventDefault(); $('drop').classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (n) { $('drop').addEventListener(n, function (e) { e.preventDefault(); $('drop').classList.remove('over'); }); });
    document.addEventListener('dragover', function (e) { e.preventDefault(); });
    document.addEventListener('drop', function (e) { e.preventDefault(); });

    wireSegmented('method', function (v) { show('ghost-warn', v === 'ghost' || v === 'all'); });
    wireSegmented('tags');
    $('btn-advanced').addEventListener('click', function () { var o = $('advanced').classList.toggle('hidden'); $('btn-advanced').classList.toggle('open', !o); });
    $('btn-outdir').addEventListener('click', function () { api().pick_dir().then(function (p) { if (p) { $('outdir').value = p; savePrefs(); } }); });
    $('btn-prepare').addEventListener('click', onPrepare);
    $('btn-check').addEventListener('click', onCheck);
    $('btn-compare').addEventListener('click', onCompare);
    $('btn-open').addEventListener('click', function () { if (state.outDir) api().open_folder(state.outDir); });
    $('btn-log').addEventListener('click', function () { state.logOpen = $('log').classList.toggle('hidden') === false; $('log-chev').classList.toggle('open', state.logOpen); });
    $('btn-theme').addEventListener('click', function () { applyTheme(state.theme === 'dark' ? 'light' : 'dark'); savePrefs(); });
    $('btn-lang').addEventListener('click', function () { applyLang(state.lang === 'ar' ? 'en' : 'ar'); savePrefs(); });
  }

  function boot() {
    api().get_prefs().then(function (prefs) {
      state.prefs = prefs || {};
      applyTheme(state.prefs.theme || 'dark');
      applyLang(state.prefs.lang || 'ar');
      if (state.prefs.outDir) $('outdir').value = state.prefs.outDir;
    });
    api().info().then(function (info) {
      $('version').textContent = 'v' + info.version;
      if (!info.tools_ok) { toast(t('tools_missing'), 8000); appendLog('ERROR: ' + (info.tools_error || '')); show('log', true); }
      else { $('tools-line').textContent = 'ffmpeg: ' + info.ffmpeg; }
    });
    schedulePoll(200);
  }

  document.addEventListener('DOMContentLoaded', function () {
    applyLang('ar'); wire();
    if (window.pywebview && window.pywebview.api) boot();
    else window.addEventListener('pywebviewready', boot);
  });
})();
