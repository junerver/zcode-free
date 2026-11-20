/* 场次表页:分组行(启用/时间/重复/覆盖/结果) + 场次日历(即时保存) */
(function () {
  const $ = (id) => document.getElementById(id);
  window.PAGES = window.PAGES || {};
  let slots = [];
  let targets = [];          // [{id,name,enabled}],场次可以只跑其中某一个
  let lastResults = {};
  // 日历状态:当前查看的年月(默认今天),以及「日期 → 当天结果等级」映射
  let calYear = null;
  let calMonth = null;          // 0 起:0=一月
  let dayMarks = {};            // 'YYYY-MM-DD' → 3=已领 2=未成功 1=无需领

  // 星期编号沿用 Python 的 weekday():周一=0 … 周日=6,顺序与后端一致
  const DAY_NAMES = ['一', '二', '三', '四', '五', '六', '日'];
  const DAY_ORDER = [4, 5, 6, 0, 1, 2, 3];   // 展示顺序从周五起(与福利周期一致)

  function targetName(id) {
    if (!id) return '全部目标';
    for (let i = 0; i < targets.length; i += 1) {
      if (targets[i].id === id) return targets[i].name || targets[i].id;
    }
    return String(id);
  }

  // 执行记录按「时间|目标」归档;同一分钟排多个目标时各自一条,不会互相顶掉
  function resultKey(slot) {
    return (slot.time || '') + '|' + (slot.target || '');
  }

  // 一行场次对应到今天的哪条记录:指定了目标就精确匹配,「全部目标」则挑最扎眼的那条
  function slotResult(slot) {
    if (slot.target) return lastResults[resultKey(slot)] || null;
    const mine = Object.keys(lastResults)
      .filter((key) => key.indexOf((slot.time || '') + '|') === 0)
      .map((key) => lastResults[key]);
    if (!mine.length) return null;
    const WORST = ['failed', 'aborted', 'need_manual', 'not_available', 'skipped', 'dry_run'];
    let picked = mine[0];
    WORST.some((status) => {
      const hit = mine.find((item) => item.status === status);
      if (hit) { picked = hit; return true; }
      return false;
    });
    return picked;
  }

  /* 悬停说明:一行可能跑了多个目标,把这一场今天的所有记录都列出来,
     不然「全部目标」那行只显示其中一条,看不出另外几个应用是什么结果。 */
  function resultTip(slot, result) {
    const list = slot.target
      ? [result]
      : Object.keys(lastResults)
        .filter((key) => key.indexOf((slot.time || '') + '|') === 0)
        .map((key) => lastResults[key]);
    const rows = list.filter((item) => !!item);
    if (!rows.length) return `${slot.time || '--'} 今天还没有执行记录`;
    return rows.map((item) => {
      const info = window.UITL.statusInfo(item.status);
      const who = item.target_name || targetName(item.target || '');
      return `${slot.time} · ${who}:${info.text}${item.summary ? ' — ' + item.summary : ''}`;
    }).join('\n');
  }

  // 「全部目标」= 按签到目标页的顺序挨个跑;指定 id 则这一场只跑那一个
  function targetSelect(value) {
    const options = ['<option value="">全部目标</option>'].concat(targets.map((t) =>
      `<option value="${window.UITL.esc(t.id)}"${t.id === value ? ' selected' : ''}>${window.UITL.esc(t.name || t.id)}</option>`));
    return `<select class="slot-target" title="这一场跑哪些目标">${options.join('')}</select>`;
  }

  /* 后端按「时间 + 星期 + 目标」去重:同一分钟可以排不同目标、不同星期。
     前端提前查出来提醒,免得用户加了半天发现少了一行。 */
  function dupeCount(list) {
    const seen = {};
    let dupes = 0;
    list.forEach((item) => {
      const picked = (item.days && item.days.length) ? item.days.slice().sort((a, b) => a - b).join('') : 'a';
      const key = [item.time, picked, item.target || ''].join('|');
      if (seen[key]) dupes += 1;
      seen[key] = true;
    });
    return dupes;
  }

  function collect() {
    const rows = $('slotTable').querySelectorAll('.slot-row');
    const out = [];
    rows.forEach((row) => {
      const time = row.querySelector('input[type="time"]').value || '10:00';
      const enabled = row.querySelector('input[type="checkbox"]').checked;
      const attemptsRaw = row.querySelector('.attempts').value.trim();
      const gapRaw = row.querySelector('.gap').value.trim();
      const target = row.querySelector('.slot-target').value;
      // 「每天」时不写 days(保持与旧配置一致);选了部分星期才落字段
      const picked = [];
      row.querySelectorAll('.days .day.on').forEach((btn) => {
        picked.push(Number(btn.dataset.day));
      });
      const entry = {
        time,
        enabled,
        target,
        attempts: attemptsRaw ? Number(attemptsRaw) : null,
        retry_gap_s: gapRaw ? Number(gapRaw) : null,
      };
      if (picked.length && picked.length < 7) entry.days = picked.sort((a, b) => a - b);
      out.push(entry);
    });
    return out;
  }

  async function save(showToast) {
    slots = collect();
    const dupes = dupeCount(slots);
    try {
      await window.API.call('save_schedule', { slots });
      if (showToast) window.APP.toast('场次表已更新', 'ok');
      if (dupes) window.APP.toast(dupes + ' 个场次与已有行的时间、星期、目标都相同,重复的那条会被忽略', 'err');
    } catch (err) {
      window.APP.toast('保存失败:' + err.message, 'err');
    }
  }

  /* ---------- 一行场次的 DOM ---------- */

  function buildRow(slot, index) {
    const picked = (slot.days && slot.days.length && slot.days.length < 7) ? slot.days : null;
    // 未指定星期 = 每天:按钮全灰(靠 .all 的样式传达),并加「每天」说明
    const daysHtml = DAY_ORDER.map((d) => {
      const on = !!picked && picked.indexOf(d) >= 0;
      return `<button type="button" class="day${on ? ' on' : ''}${picked ? '' : ' all'}"
                data-day="${d}" title="周${DAY_NAMES[d]}">${DAY_NAMES[d]}</button>`;
    }).join('');

    // 「最近结果」只看今天的记录:历史场次的旧结果挂在这里会被误读成"刚刚又失败了"。
    // 同一分钟排了多个目标时它们各有一条记录,按目标挑,不会互相顶掉
    const result = slotResult(slot);
    const info = result ? window.UITL.statusInfo(result.status) : null;
    // 只显示「今天」的记录:否则改完场次后,这一列还挂着上次触发的旧结果,
    // 看起来像是刚失败/被跳过(实际是几天前那场的)
    const resultHtml = info
      ? `<span class="st ${info.cls}">${window.UITL.esc(info.text)}</span>` +
        `<span class="st-t"> · ${window.UITL.esc(window.UITL.dt(result.started_at))}</span>` +
        // 「全部目标」的一行会跑好几个应用,得说明这条结果是谁的
        (slot.target || !result.target_name ? ''
          : `<span class="st-t"> · ${window.UITL.esc(result.target_name)}</span>`)
      : '<span class="st muted">今日未执行</span>';

    const row = document.createElement('div');
    row.className = 'slot-row' + (slot.enabled === false ? ' off' : '');
    row.innerHTML = `
      <label class="sw" title="启用 / 停用这一场">
        <input type="checkbox" ${slot.enabled !== false ? 'checked' : ''} /><i></i>
      </label>
      <input type="time" class="slot-time" value="${window.UITL.esc(slot.time || '10:00')}" />
      <div class="days" title="点击切换星期;全灰=每天">
        <div class="day-set">${daysHtml}</div>
        <span class="every${picked ? ' hidden' : ''}">每天</span>
      </div>
      <div class="slot-target-wrap">${targetSelect(slot.target || '')}</div>
      <div class="mini-group" title="留空表示用设置页里的默认值">
        <label class="mini"><input type="number" class="attempts" min="1" max="20" placeholder="默认"
          value="${slot.attempts != null ? window.UITL.esc(slot.attempts) : ''}" /><span>次</span></label>
        <label class="mini"><input type="number" class="gap" min="1" max="120" placeholder="默认"
          value="${slot.retry_gap_s != null ? window.UITL.esc(slot.retry_gap_s) : ''}" /><span>秒</span></label>
      </div>
      <div class="slot-result" title="${window.UITL.esc(resultTip(slot, result))}">${resultHtml}</div>
      <button class="slot-del" title="删除场次" aria-label="删除场次">删除</button>`;

    row.querySelector('input[type="checkbox"]').addEventListener('change', () => {
      row.classList.toggle('off', !row.querySelector('input[type="checkbox"]').checked);
      save(true);
    });
    row.querySelector('input[type="time"]').addEventListener('change', () => save(true));
    row.querySelector('.slot-target').addEventListener('change', () => {
      slots = collect();
      save(true);
      render();          // 目标变了,日历胶囊和「最近结果」那一列都得跟着换
    });
    row.querySelector('.attempts').addEventListener('change', () => save(true));
    row.querySelector('.gap').addEventListener('change', () => save(true));

    // 星期按钮:没有任何 .on 表示「每天」;点了某个星期就变成"只跑这些星期"。
    // 从「每天」点第一下时,意图必然是"只排这一天",所以直接切成单天;
    // 已是自定义状态时按普通开关切换,全部取消则回到「每天」。
    const dayBtns = row.querySelectorAll('.days .day');
    const applyDays = () => {
      const on = [...dayBtns].filter((b) => b.classList.contains('on'));
      dayBtns.forEach((b) => {
        b.classList.toggle('all', on.length === 0);
        b.classList.toggle('on', on.length > 0 && b.classList.contains('on'));
      });
      row.querySelector('.every').classList.toggle('hidden', on.length > 0);
    };
    dayBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        const custom = [...dayBtns].some((b) => b.classList.contains('on'));
        if (!custom) {
          btn.classList.add('on');            // 每天 → 只留这一天
        } else {
          btn.classList.toggle('on');
        }
        applyDays();
        save(true);
        buildCalendar();
      });
    });

    row.querySelector('.slot-del').addEventListener('click', () => {
      slots.splice(index, 1);
      render();
      save(true);
    });
    return row;
  }

  function render() {
    const list = $('slotTable');
    list.querySelectorAll('.slot-row').forEach((n) => n.remove());
    $('slotEmpty').classList.toggle('hidden', slots.length > 0);
    list.classList.toggle('empty-list', slots.length === 0);
    const countEl = $('slotCount');
    if (countEl) {
      const on = slots.filter((s) => s.enabled !== false).length;
      countEl.textContent = slots.length + ' 个场次 · 启用 ' + on + ' 个';
    }

    slots.forEach((slot, index) => {
      list.appendChild(buildRow(slot, index));
    });
    buildCalendar();
  }

  /* ---------- 场次日历:整月网格,过去标结果、未来标计划 ---------- */

  const pad2 = (n) => String(n).padStart(2, '0');
  const dateKey = (y, m, d) => `${y}-${pad2(m + 1)}-${pad2(d)}`;

  /* 把会话记录折成「每天一个结论」,取当天最重的那条:
     成功 > 失败/中止/需人工 > 跳过/无可领/演练 */
  function rankOf(status) {
    if (status === 'success') return 3;
    if (status === 'failed' || status === 'need_manual' || status === 'aborted') return 2;
    if (status === 'not_available' || status === 'skipped' || status === 'dry_run') return 1;
    return 0;
  }
  const MARK_HTML = {
    3: '<span class="cal-tag ok">已领</span>',
    2: '<span class="cal-tag fail">未成功</span>',
    1: '<span class="cal-tag muted">无需领</span>',
  };

  function buildCalendar() {
    if (calYear === null) {
      const now = new Date();
      calYear = now.getFullYear();
      calMonth = now.getMonth();
    }
    const now = new Date();
    const todayKey = dateKey(now.getFullYear(), now.getMonth(), now.getDate());

    const enabled = slots.filter((s) => s.enabled !== false && s.time);
    const firstPyDay = (new Date(calYear, calMonth, 1).getDay() + 6) % 7;   // 周一=0
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const rows = Math.ceil((firstPyDay + daysInMonth) / 7);

    $('calTitle').textContent = `${calYear} 年 ${calMonth + 1} 月`;
    const viewingNow = calYear === now.getFullYear() && calMonth === now.getMonth();
    $('calToday').classList.toggle('hidden', viewingNow);

    const grid = $('calGrid');
    grid.innerHTML = '';
    const prevMonthDays = new Date(calYear, calMonth, 0).getDate();
    for (let i = 0; i < rows * 7; i += 1) {
      const dayNum = i - firstPyDay + 1;
      const cell = document.createElement('div');
      if (dayNum < 1 || dayNum > daysInMonth) {
        // 上/下月的补位格:显示相邻月的真实日期,内容留空
        const adj = dayNum < 1 ? prevMonthDays + dayNum : dayNum - daysInMonth;
        cell.className = 'cal-cell dim';
        cell.innerHTML = `<span class="cal-num">${adj}</span>`;
        grid.appendChild(cell);
        continue;
      }
      const key = dateKey(calYear, calMonth, dayNum);
      const pyDay = i % 7;                        // 表头从周一开始,列号即 Python 星期
      const planned = enabled
        .filter((s) => !s.days || !s.days.length || s.days.indexOf(pyDay) >= 0)
        .sort((a, b) => String(a.time).localeCompare(String(b.time)));
      const parts = [];
      if (planned.length) {
        parts.push('<div class="cal-slots">' + planned
          .map((s) => `<span class="week-slot" title="${window.UITL.esc(s.time + ' · ' + targetName(s.target || ''))}">` +
            `${window.UITL.esc(s.time)}` +
            // 只排了某一个目标时把名字挂在时间后面,全部目标留空
            (s.target ? `<b class="ws-tgt">${window.UITL.esc(targetName(s.target))}</b>` : '') +
            '</span>').join('') + '</div>');
      }
      if (dayMarks[key]) parts.push(`<div class="cal-marks">${MARK_HTML[dayMarks[key]]}</div>`);
      cell.className = 'cal-cell' + (key === todayKey ? ' today' : '');
      cell.innerHTML = `<span class="cal-num">${dayNum}</span>${parts.join('')}`;
      grid.appendChild(cell);
    }
  }

  function shiftMonth(delta) {
    if (calYear === null) buildCalendar();
    const d = new Date(calYear, calMonth + delta, 1);
    calYear = d.getFullYear();
    calMonth = d.getMonth();
    buildCalendar();
  }

  window.PAGES.schedule = {
    init() {
      document.getElementById('addSlot').addEventListener('click', () => {
        slots.push({ time: '10:00', enabled: true, target: '', attempts: null, retry_gap_s: null });
        render();
        save(true);
      });
      $('calPrev').addEventListener('click', () => shiftMonth(-1));
      $('calNext').addEventListener('click', () => shiftMonth(1));
      $('calToday').addEventListener('click', () => {
        const now = new Date();
        calYear = now.getFullYear();
        calMonth = now.getMonth();
        buildCalendar();
      });
    },

    async onShow() {
      const cfg = await window.API.call('get_schedule');
      slots = (cfg && cfg.slots) || [];
      // 目标下拉的数据源:优先用 get_schedule 顺带返回的 targets,没有再单独取一次
      targets = (cfg && cfg.targets) || [];
      if (!targets.length) {
        try {
          const all = await window.API.call('get_targets');
          targets = (all && all.targets) || [];
        } catch (err) { targets = []; }
      }
      try {
        const data = await window.API.call('list_sessions', { limit: 200 });
        lastResults = {};
        dayMarks = {};
        const today = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        const todayStr = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
        (data && data.items ? data.items : []).forEach((item) => {
          const started = String(item.started_at || '');
          if (item.slot && started.startsWith(todayStr)) {
            // 记录按「时间|目标」归档:同一分钟排多个目标时各留各的,后面的不会被顶掉
            const key = item.slot + '|' + (item.target || '');
            if (!lastResults[key]) lastResults[key] = item;
          }
          // 日历则要整月的结果:按日期折成当天最重的一个结论
          if (started.length >= 10) {
            const key = started.slice(0, 10);
            dayMarks[key] = Math.max(dayMarks[key] || 0, rankOf(item.status));
          }
        });
      } catch (err) { lastResults = {}; dayMarks = {}; }
      render();
    },
  };
})();
