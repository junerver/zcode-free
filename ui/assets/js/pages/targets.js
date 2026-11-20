/* 签到目标页:目标列表 + 单目标配置 + 坐标录制器(签到点击 / 领取后补关) */
(function () {
  const $ = (id) => document.getElementById(id);
  window.PAGES = window.PAGES || {};

  const FLOW_LABELS = { vision: '视觉识别', steps: '固定坐标' };
  const ONCE_LABELS = { none: '不限周期', daily: '每天一次', weekly: '每周一次' };
  const FIND_LABELS = { process: '按进程', title: '按标题' };
  const VERIFY_LABELS = { vision: '视觉复核', none: '不校验' };
  const ANCHOR_LABELS = {
    window: '整窗比例', top_left: '左上角', bottom_left: '左下角',
    top_right: '右上角', bottom_right: '右下角',
  };
  // 提交时只带这些字段(后端 normalize 也只认它们),运行态与周期记账不回写
  const CONFIG_KEYS = ['id', 'name', 'enabled', 'exe', 'find_by', 'window_title_match',
    'fullscreen', 'startup_wait_s', 'flow', 'verify', 'once', 'steps', 'post_steps'];

  let targets = [];
  let opts = { anchors: [], flows: [], verifies: [], once_modes: [] };
  let activeId = '';
  let shot = null;            // capture_target 回来的底图 { image, rect, size, window }
  let recMode = 'steps';      // 图上点一下记到哪张表:steps | post
  let probeByIdx = {};        // 校验结果:签到点击步骤序号(1 起) → 点是否还在窗口内
  let probePost = {};         // 同上,补关点
  let saving = false;

  function numOr(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) && String(value).trim() !== '' ? n : fallback;
  }

  const fmtNum = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : '';
  };

  function baseName(path) {
    const parts = String(path || '').split(/[\\/]/);
    return parts[parts.length - 1] || '';
  }

  function indexOf(id) {
    for (let i = 0; i < targets.length; i += 1) if (targets[i].id === id) return i;
    return -1;
  }

  function active() { return targets[indexOf(activeId)] || null; }

  function toPayload(t) {
    const out = {};
    CONFIG_KEYS.forEach((key) => { if (t[key] !== undefined) out[key] = t[key]; });
    const mapStep = (s) => ({
      id: s.id, label: s.label, anchor: s.anchor,
      dx: numOr(s.dx, 0), dy: numOr(s.dy, 0), wait_after_s: numOr(s.wait_after_s, 0),
    });
    out.steps = (t.steps || []).map(mapStep);
    out.post_steps = (t.post_steps || []).map(mapStep);
    return out;
  }

  /* ---------- 保存 ---------- */

  // 后端 save_targets 是整表替换(顺序即执行顺序),回传的 TargetView 直接作为新的本地副本。
  // id 会被清洗(小写、去空格、重名追加序号),所以保存后按 preferId 重新定位选中项。
  async function save(showToast, preferId) {
    if (saving) return false;
    saving = true;
    let ok = false;
    try {
      const res = await window.API.call('save_targets', { targets: targets.map(toPayload) });
      if (res && res.ok) {
        ok = true;
        targets = res.targets || [];
        const want = preferId || activeId;
        activeId = indexOf(want) >= 0 ? want : (targets.length ? targets[0].id : '');
        renderList();
        if (showToast) window.APP.toast('签到目标已保存', 'ok');
      } else {
        window.APP.toast('保存失败:' + ((res && res.message) || '未知原因'), 'err');
      }
    } catch (err) {
      window.APP.toast('保存失败:' + err.message, 'err');
    } finally {
      saving = false;
    }
    return ok;
  }

  /* ---------- 列表 ---------- */

  function statusBadge(t) {
    const notes = [];
    if (!t.exe) notes.push('未设置程序路径');
    else if (t.executable_missing) notes.push('路径不存在');
    if (t.flow === 'steps' && !t.steps_count) notes.push('未录制坐标');
    const text = notes.join(' · ');
    return text ? `<span class="tgt-warn" title="${window.UITL.esc(text)}">${window.UITL.esc(text)}</span>` : '';
  }

  function renderList() {
    const list = $('targetList');
    list.querySelectorAll('.tgt-row').forEach((n) => n.remove());
    $('targetEmpty').classList.toggle('hidden', targets.length > 0);
    list.classList.toggle('empty-list', targets.length === 0);
    $('targetCount').textContent = targets.length + ' 个目标 · 启用 ' +
      targets.filter((t) => t.enabled !== false).length + ' 个';

    targets.forEach((t, i) => {
      const row = document.createElement('div');
      row.className = 'tgt-row' + (t.id === activeId ? ' active' : '') +
        (t.enabled === false ? ' off' : '');
      row.dataset.id = t.id;
      const steps = (t.flow === 'steps'
        ? `<span class="mono">${t.steps_count || 0} 步</span>`
        : '<span class="muted">视觉</span>')
        + (t.post_steps_count ? `<span class="mono muted">+补关 ${t.post_steps_count}</span>` : '');
      row.innerHTML = `
        <span class="mono tgt-order">${i + 1}</span>
        <button type="button" class="tgt-pick" data-act="pick" title="编辑这个目标">
          <b>${window.UITL.esc(t.name || t.id)}</b><i class="mono">${window.UITL.esc(t.id)}</i>
        </button>
        <span class="mono tgt-exe" title="${window.UITL.esc(t.exe || '')}">${window.UITL.esc(baseName(t.exe)) || '未设置'}</span>
        <span class="badge flow-${window.UITL.esc(t.flow)}">${window.UITL.esc(FLOW_LABELS[t.flow] || t.flow)}</span>
        <span class="badge period-${window.UITL.esc(t.once)}">${window.UITL.esc(ONCE_LABELS[t.once] || t.once)}</span>
        <span class="tgt-steps">${steps}</span>
        <span class="tgt-status">${statusBadge(t)}<i class="st-dot${t.running ? ' on' : ''}" title="${t.running ? '进程在运行' : '未运行'}"></i></span>
        <label class="sw" title="启用 / 停用"><input type="checkbox" ${t.enabled !== false ? 'checked' : ''} /><i></i></label>
        <div class="tgt-move">
          <button class="btn small" type="button" data-act="up" ${i === 0 ? 'disabled' : ''}>上移</button>
          <button class="btn small" type="button" data-act="down" ${i === targets.length - 1 ? 'disabled' : ''}>下移</button>
        </div>
        <button class="slot-del" type="button" data-act="del">删除</button>`;
      list.appendChild(row);
    });
  }

  function move(id, delta) {
    const i = indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= targets.length) return;
    const moved = targets.splice(i, 1)[0];
    targets.splice(j, 0, moved);
    renderList();
    save(false, id);
  }

  async function remove(id) {
    const t = targets[indexOf(id)];
    if (!t) return;
    if (targets.length <= 1) {
      window.APP.toast('至少需要保留一个签到目标', 'err');
      return;
    }
    const name = t.name || t.id;
    if (!(await window.APP.confirm(`确定删除签到目标「${name}」吗?\n它的录制坐标与周期记账一并移除,引用它的场次会回落到「全部目标」。`))) return;
    targets = targets.filter((x) => x.id !== id);
    if (activeId === id) {
      activeId = targets.length ? targets[0].id : '';
      renderForm();
      renderRecorder();
    }
    await save(true, '');
  }

  function onListClick(event) {
    const btn = event.target.closest('[data-act]');
    if (!btn) return;
    const row = btn.closest('.tgt-row');
    if (!row) return;
    const id = row.dataset.id;
    const act = btn.dataset.act;
    if (act === 'pick') { activeId = id; renderForm(); renderRecorder(); renderList(); }
    else if (act === 'up') move(id, -1);
    else if (act === 'down') move(id, 1);
    else if (act === 'del') remove(id);
  }

  function onListChange(event) {
    const input = event.target;
    if (!input.matches('.sw input')) return;
    const row = input.closest('.tgt-row');
    const t = row && targets[indexOf(row.dataset.id)];
    if (!t) return;
    t.enabled = input.checked;
    row.classList.toggle('off', !t.enabled);
    save(false, t.id);
  }

  /* ---------- 详情表单 ---------- */

  function fillSelect(el, values, labels, current) {
    el.innerHTML = '';
    (values || []).forEach((v) => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = labels[v] || v;
      el.appendChild(opt);
    });
    el.value = values.indexOf(current) >= 0 ? current : (values[0] || '');
  }

  function renderOptions() {
    fillSelect($('tgFindBy'), ['process', 'title'], FIND_LABELS, 'process');
    fillSelect($('tgFlow'), opts.flows.length ? opts.flows : ['vision', 'steps'], FLOW_LABELS, 'vision');
    fillSelect($('tgVerify'), opts.verifies.length ? opts.verifies : ['vision', 'none'], VERIFY_LABELS, 'vision');
    fillSelect($('tgOnce'), opts.once_modes.length ? opts.once_modes : ['none', 'daily', 'weekly'], ONCE_LABELS, 'none');
  }

  function renderForm() {
    const t = active();
    const fields = ['tgName', 'tgId', 'tgExe', 'tgFindBy', 'tgTitle', 'tgFullscreen', 'tgWait',
      'tgFlow', 'tgVerify', 'tgOnce'];
    fields.forEach((id) => { $(id).disabled = !t; });
    const buttons = ['btnDetectTarget', 'btnSaveTarget', 'btnRunTarget', 'btnRunTargetDry'];
    buttons.forEach((id) => { $(id).disabled = !t; });
    $('editTargetName').textContent = t ? `${t.name || t.id}(${t.id})` : '未选择目标';
    if (!t) {
      ['tgName', 'tgId', 'tgExe', 'tgTitle'].forEach((id) => { $(id).value = ''; });
      $('tgWait').value = '';
      return;
    }
    $('tgName').value = t.name || '';
    $('tgId').value = t.id || '';
    $('tgExe').value = t.exe || '';
    $('tgFindBy').value = t.find_by || 'process';
    $('tgTitle').value = t.window_title_match || '';
    $('tgFullscreen').checked = t.fullscreen !== false;
    $('tgWait').value = t.startup_wait_s != null ? t.startup_wait_s : 5;
    $('tgFlow').value = t.flow || 'vision';
    $('tgVerify').value = t.verify || 'vision';
    $('tgOnce').value = t.once || 'none';
  }

  function collectForm() {
    const t = active();
    if (!t) return null;
    const newId = $('tgId').value.trim().toLowerCase();
    t.name = $('tgName').value.trim();
    t.exe = $('tgExe').value.trim();
    t.find_by = $('tgFindBy').value;
    t.window_title_match = $('tgTitle').value.trim();
    t.fullscreen = $('tgFullscreen').checked;
    t.startup_wait_s = Math.max(0, numOr($('tgWait').value, 5));
    t.flow = $('tgFlow').value;
    t.verify = $('tgVerify').value;
    t.once = $('tgOnce').value;
    if (newId && newId !== t.id) t.id = newId;
    return t;
  }

  // 表单改动即整表保存(与其他页一致),保存后只刷新列表,不动输入框焦点
  async function onFieldChange() {
    const t = collectForm();
    if (!t) return;
    await save(false, t.id);
    renderRecorder();
  }

  /* ---------- 探测路径 ---------- */

  async function detectPath() {
    const t = active();
    if (!t) return;
    await save(false, t.id);
    const box = $('tgtDetectResult');
    box.classList.remove('hidden', 'ok', 'err');
    box.textContent = '探测中(首次可能需要几秒)…';
    try {
      const res = await window.API.call('detect_target', {
        target: t.id, path: $('tgExe').value.trim() || undefined,
      });
      box.classList.add(res && res.ok ? 'ok' : 'err');
      box.textContent = (res && res.message) || '未知结果';
      if (res && res.ok) {
        $('tgExe').value = res.path;
        t.exe = res.path;
        await save(false, t.id);
      }
    } catch (err) {
      box.classList.add('err');
      box.textContent = '探测失败:' + err.message;
    }
  }

  /* ---------- 坐标录制 ---------- */

  function renderRecorder() {
    const t = active();
    $('recorderPanel').classList.toggle('hidden', !t);
    if (!t) { shot = null; probeByIdx = {}; probePost = {}; }
    $('recTargetName').textContent = t ? (t.name || t.id) : '—';
    // 视觉识别的目标没有「签到点击」可录,但补关点照样要录:底图还是那张底图
    const stepsFlow = !!t && t.flow === 'steps';
    $('claimSteps').classList.toggle('hidden', !stepsFlow);
    if (!stepsFlow) recMode = 'post';
    renderMode();
    renderShot();
    renderSteps();
  }

  function renderMode() {
    const btns = $('recMode').querySelectorAll('[data-rmode]');
    Array.prototype.forEach.call(btns, (btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.rmode === recMode));
    });
    $('recModeHint').textContent = recMode === 'post'
      ? '在「领取成功」那一屏上点浮窗的关闭按钮,每点一次记一个补关点。'
      : '在图上依次点击要按的位置,每点一次记一步。';
  }

  function renderShot() {
    const img = $('recImage');
    const has = !!shot;
    img.classList.toggle('hidden', !has);
    $('recNoShot').classList.toggle('hidden', has);
    if (has) img.src = shot.image;
    else img.removeAttribute('src');
    drawMarks();
  }

  /* 把一步的锚点 + 偏移换算回底图上的像素(与 core/coords.resolve 同一套规则)。
     录制点在图上显示,才看得出"点偏了"——只靠日志是量不出来的。 */
  function pointInImage(step) {
    if (!shot) return null;
    const w = Number(shot.size[0]) || 0;
    const h = Number(shot.size[1]) || 0;
    const anchor = step.anchor || 'window';
    const dx = Number(step.dx) || 0;
    const dy = Number(step.dy) || 0;
    if (anchor === 'window') return [dx * w, dy * h];
    if (anchor === 'top_left') return [dx, dy];
    if (anchor === 'top_right') return [w - dx, dy];
    if (anchor === 'bottom_left') return [dx, h - dy];
    if (anchor === 'bottom_right') return [w - dx, h - dy];
    return null;
  }

  function drawMarks() {
    const box = $('recMarks');
    box.innerHTML = '';
    const t = active();
    if (!shot || !t) return;
    const w = Number(shot.size[0]) || 1;
    const h = Number(shot.size[1]) || 1;
    const add = (list, probe, isPost) => (list || []).forEach((step, i) => {
      const p = pointInImage(step);
      if (!p) return;
      const mark = document.createElement('span');
      mark.className = 'rec-mark' + (isPost ? ' post' : '') + (probe[i + 1] === false ? ' bad' : '');
      mark.style.left = (p[0] / w) * 100 + '%';
      mark.style.top = (p[1] / h) * 100 + '%';
      const num = document.createElement('i');
      num.textContent = isPost ? ('关' + (i + 1)) : String(i + 1);
      const tag = document.createElement('span');
      tag.textContent = step.label || (isPost ? '补关' : ('第 ' + (i + 1) + ' 步'));   // 用户输入,走 textContent
      mark.appendChild(num);
      mark.appendChild(tag);
      box.appendChild(mark);
    });
    if (t.flow === 'steps') add(t.steps, probeByIdx, false);
    add(t.post_steps, probePost, true);
  }

  function renderSteps() {
    const t = active();
    const steps = (t && t.flow === 'steps') ? (t.steps || []) : [];
    const post = (t && t.post_steps) || [];
    $('recStepCount').textContent = steps.length + ' 步签到'
      + (post.length ? ' · ' + post.length + ' 步补关' : '');
    $('stepsEmpty').classList.toggle('hidden', steps.length > 0);
    $('postStepsEmpty').classList.toggle('hidden', post.length > 0);
    fillRows($('stepList'), steps, probeByIdx, '');
    fillRows($('postStepList'), post, probePost, '补关');
    drawMarks();
  }

  function fillRows(box, steps, probe, prefix) {
    box.innerHTML = '';
    steps.forEach((step, i) => {
      const row = document.createElement('div');
      row.className = 'rec-step' + (probe[i + 1] === false ? ' bad' : '');
      row.innerHTML = `
        <span class="mono rec-no">${prefix ? '关' + (i + 1) : i + 1}</span>
        <input type="text" class="st-label" value="${window.UITL.esc(step.label || '')}" placeholder="${prefix}第 ${i + 1} 步" />
        <select class="st-anchor">${opts.anchors.map((a) =>
          `<option value="${window.UITL.esc(a)}">${window.UITL.esc(ANCHOR_LABELS[a] || a)}</option>`).join('')}</select>
        <input type="number" class="mono st-dx" step="1" value="${window.UITL.esc(fmtNum(step.dx))}" aria-label="横向偏移" />
        <input type="number" class="mono st-dy" step="1" value="${window.UITL.esc(fmtNum(step.dy))}" aria-label="纵向偏移" />
        <input type="number" class="mono st-wait" min="0" step="0.1" value="${window.UITL.esc(fmtNum(step.wait_after_s))}" aria-label="点后等待" />
        <div class="tgt-move">
          <button class="btn small" type="button" data-sact="up" ${i === 0 ? 'disabled' : ''}>上移</button>
          <button class="btn small" type="button" data-sact="down" ${i === steps.length - 1 ? 'disabled' : ''}>下移</button>
          <button class="btn small" type="button" data-sact="del">删除</button>
        </div>`;
      row.querySelector('.st-anchor').value = step.anchor || 'window';
      row.dataset.index = String(i);
      row.dataset.slist = prefix ? 'post' : 'steps';
      box.appendChild(row);
    });
  }

  function setRecMessage(text, kind) {
    const box = $('recMessage');
    box.className = 'result' + (kind ? ' ' + kind : '');
    box.textContent = text || '';
  }

  async function capture(launch) {
    const t = active();
    if (!t) return;
    setRecMessage(launch ? '正在启动目标并等待界面加载(最长几十秒)…' : '置顶窗口并截图中…');
    await save(false, t.id);
    let res = null;
    try {
      res = await window.API.call('capture_target', { target: t.id, launch: !!launch });
    } catch (err) {
      setRecMessage('截图失败:' + err.message, 'err');
      return;
    }
    $('btnCaptureLaunch').classList.toggle('hidden', !!(res && res.ok));
    if (!res || !res.ok) {
      setRecMessage(((res && res.message) || '截图失败') + '(也可先手动打开目标应用,再点「截图」)', 'err');
      return;
    }
    shot = { image: res.image, rect: res.rect, size: res.size, window: res.window };
    probeByIdx = {};
    const flat = Number(res.uniform_ratio) > 0.98 ? ' · 画面接近纯色,等界面渲染出来再点' : '';
    setRecMessage(`${res.message}${flat} · 在图上依次点击要按的位置`, 'ok');
    renderShot();
  }

  async function onImageClick(event) {
    const t = active();
    if (!t) return;
    if (!shot) { setRecMessage('先点右上「截图」拿到窗口图,再在图上点位置。', 'err'); return; }
    const isPost = recMode === 'post';
    const key = isPost ? 'post_steps' : 'steps';
    const img = $('recImage');
    const box = img.getBoundingClientRect();
    if (!box.width || !box.height) return;
    // 图是按面板宽度缩放显示的,先把显示尺寸换算回图片像素(1 图片像素 = 1 屏幕像素)
    const x = (event.clientX - box.left) / box.width * Number(shot.size[0]);
    const y = (event.clientY - box.top) / box.height * Number(shot.size[1]);
    if (x < 0 || y < 0 || x > shot.size[0] || y > shot.size[1]) return;
    const index = (t[key] || []).length + 1;
    let res = null;
    try {
      res = await window.API.call('record_point', { target: t.id, rect: shot.rect, x, y, index });
    } catch (err) {
      setRecMessage('录制失败:' + err.message, 'err');
      return;
    }
    if (!res || !res.ok) { setRecMessage((res && res.message) || '录制失败', 'err'); return; }
    const step = res.step;
    t[key] = (t[key] || []).concat([{
      id: (isPost ? 'post' : 'step') + index,
      label: step.label || ((isPost ? '补关第 ' : '第 ') + index + ' 步'),
      anchor: step.anchor, dx: step.dx, dy: step.dy, wait_after_s: step.wait_after_s,
    }]);
    await save(false, t.id);
    renderSteps();
    setRecMessage(`已录制${isPost ? '补关点' : '第 ' + index + ' 步'}:${step.anchor},偏移(${step.dx},${step.dy})· 记得点「保存目标」`, 'ok');
  }

  // 行属于哪张表:签到点击还是补关点(两张表控件一模一样,靠行的标记区分)
  function listKey(row) {
    return row && row.dataset.slist === 'post' ? 'post_steps' : 'steps';
  }

  function listFor(t, row) {
    const key = listKey(row);
    if (!Array.isArray(t[key])) t[key] = [];
    return t[key];
  }

  function onStepsClick(event) {
    const btn = event.target.closest('[data-sact]');
    if (!btn) return;
    const row = btn.closest('.rec-step');
    const t = active();
    if (!row || !t) return;
    const list = listFor(t, row);
    const i = Number(row.dataset.index);
    if (btn.dataset.sact === 'del') list.splice(i, 1);
    else {
      const j = i + (btn.dataset.sact === 'up' ? -1 : 1);
      if (j < 0 || j >= list.length) return;
      const moved = list.splice(i, 1)[0];
      list.splice(j, 0, moved);
    }
    probeByIdx = {};
    probePost = {};
    renderSteps();
    save(false, t.id);
  }

  function onStepsChange(event) {
    const input = event.target;
    if (!input.closest('.rec-step')) return;
    const row = input.closest('.rec-step');
    const t = active();
    if (!t) return;
    const step = listFor(t, row)[Number(row.dataset.index)];
    if (!step) return;
    if (input.classList.contains('st-label')) step.label = input.value.trim();
    else if (input.classList.contains('st-anchor')) step.anchor = input.value;
    else if (input.classList.contains('st-dx')) step.dx = numOr(input.value, step.dx);
    else if (input.classList.contains('st-dy')) step.dy = numOr(input.value, step.dy);
    else if (input.classList.contains('st-wait')) step.wait_after_s = Math.max(0, numOr(input.value, 0));
    drawMarks();
    save(false, t.id);
  }

  async function probeCoords() {
    const t = active();
    if (!t) return;
    setRecMessage('按当前窗口换算各点屏幕坐标…');
    await save(false, t.id);
    let res = null;
    try {
      res = await window.API.call('probe_target', {
        target: t.id, steps: t.steps || [], post_steps: t.post_steps || [],
      });
    } catch (err) {
      setRecMessage('校验失败:' + err.message, 'err');
      return;
    }
    probeByIdx = {};
    probePost = {};
    const fill = (arr, map) => (arr || []).forEach((p) => { map[p.index] = !!p.ok; });
    fill(res && res.points, probeByIdx);
    fill(res && res.post_points, probePost);
    const bad = Object.keys(probeByIdx).filter((k) => probeByIdx[k] === false).length
      + Object.keys(probePost).filter((k) => probePost[k] === false).length;
    if (!res || !res.ok) {
      setRecMessage((res && res.message) || '校验失败', 'err');
      $('btnCaptureLaunch').classList.remove('hidden');
    } else if (res.elevated) {
      // 坐标就算全对也点不动:提权的目标只接受同等权限的鼠标注入
      setRecMessage(res.message, 'err');
    } else if (bad) {
      setRecMessage(`${res.message};${bad} 个点已落在窗口外(标红),重录或改偏移`, 'err');
    } else {
      setRecMessage(`${res.message};全部点在窗口内`, 'ok');
    }
    renderSteps();
  }

  async function runTarget(dry) {
    const t = active();
    if (!t) return;
    await save(false, t.id);
    try {
      const res = await window.API.call('run_target', { target: t.id, dry: !!dry });
      window.APP.toast((res && res.message) || '未启动', res && res.ok ? 'ok' : 'err');
      if (res && res.ok) window.APP.refreshState();
    } catch (err) {
      window.APP.toast('启动失败:' + err.message, 'err');
    }
  }

  /* ---------- 外部状态变化:只刷新徽标,不覆盖正在编辑的表单 ---------- */

  async function refreshStatus() {
    const page = $('page-targets');
    if (page && !page.classList.contains('hidden') && !saving) {
      try {
        const data = await window.API.call('get_targets');
        const byId = {};
        ((data && data.targets) || []).forEach((v) => { byId[v.id] = v; });
        targets.forEach((t) => {
          const v = byId[t.id];
          if (!v) return;
          ['running', 'period', 'steps_count', 'executable_missing', 'needs_setup', 'phases']
            .forEach((k) => { t[k] = v[k]; });
        });
        renderList();
      } catch (err) { /* 忽略:只影响状态徽标 */ }
    }
  }

  window.PAGES.targets = {
    init() {
      $('targetList').addEventListener('click', onListClick);
      $('targetList').addEventListener('change', onListChange);

      $('addTarget').addEventListener('click', async () => {
        const n = targets.length + 1;
        const fresh = { id: 'target' + n, name: '新目标 ' + n, exe: '', flow: 'vision', once: 'none' };
        targets.push(fresh);
        activeId = fresh.id;
        renderList();
        renderForm();
        renderRecorder();
        await save(true, fresh.id);
        renderForm();
      });

      $('btnSaveTarget').addEventListener('click', async () => {
        const t = collectForm();
        await save(true, t ? t.id : '');
      });
      $('btnDetectTarget').addEventListener('click', detectPath);

      ['tgName', 'tgId', 'tgExe', 'tgFindBy', 'tgTitle', 'tgFullscreen', 'tgWait',
        'tgFlow', 'tgVerify', 'tgOnce'].forEach((id) => $(id).addEventListener('change', onFieldChange));

      $('btnCapture').addEventListener('click', () => capture(false));
      $('btnCaptureLaunch').addEventListener('click', () => capture(true));
      $('btnProbe').addEventListener('click', probeCoords);
      $('btnRunTarget').addEventListener('click', () => runTarget(false));
      $('btnRunTargetDry').addEventListener('click', () => runTarget(true));
      $('recImage').addEventListener('click', onImageClick);
      $('recMode').addEventListener('click', (event) => {
        const btn = event.target.closest('[data-rmode]');
        if (!btn) return;
        recMode = btn.dataset.rmode;
        renderMode();
      });
      $('stepList').addEventListener('click', onStepsClick);
      $('stepList').addEventListener('change', onStepsChange);
      $('postStepList').addEventListener('click', onStepsClick);
      $('postStepList').addEventListener('change', onStepsChange);

      EV.on('exec_start', refreshStatus);
      EV.on('session_end', refreshStatus);
    },

    async onShow() {
      try {
        const data = await window.API.call('get_targets');
        targets = (data && data.targets) || [];
        opts = {
          anchors: (data && data.anchors) || Object.keys(ANCHOR_LABELS),
          flows: (data && data.flows) || ['vision', 'steps'],
          verifies: (data && data.verifies) || ['vision', 'none'],
          once_modes: (data && data.once_modes) || ['none', 'daily', 'weekly'],
        };
      } catch (err) {
        window.APP.toast('读取签到目标失败:' + err.message, 'err');
        return;
      }
      if (indexOf(activeId) < 0) activeId = targets.length ? targets[0].id : '';
      shot = null;
      probeByIdx = {};
      probePost = {};
      $('btnCaptureLaunch').classList.add('hidden');
      $('tgtDetectResult').classList.add('hidden');
      $('recMessage').className = 'result hidden';
      $('recMessage').textContent = '';
      renderOptions();
      renderList();
      renderForm();
      renderRecorder();
    },
  };
})();
