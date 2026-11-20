/* 概览页:统计卡片 + 上手检查 + 最近记录 */
(function () {
  const $ = (id) => document.getElementById(id);
  window.PAGES = window.PAGES || {};

  /* 每个签到目标一行:周期标签 + 本周期是否已领 + 需要留意的状态 */
  function renderTargets(items) {
    const box = $('targetPeriods');
    if (!box) return;
    box.innerHTML = '';
    const list = items || [];
    if (!list.length) {
      const empty = document.createElement('span');
      empty.className = 'tgt-none';
      empty.textContent = '未配置签到目标';
      box.appendChild(empty);
      return;
    }
    list.forEach((t) => {
      const p = t.period || {};
      const row = document.createElement('div');
      row.className = 'tgt-per' + (t.enabled === false ? ' off' : '');
      const name = document.createElement('b');
      name.className = 'tgt-per-name';
      name.textContent = t.name || t.id;
      const state = document.createElement('span');
      const notes = [];
      if (t.needs_setup) notes.push('未配置');
      if (t.executable_missing) notes.push('路径不存在');
      if (t.running) notes.push('运行中');
      if (t.enabled === false) notes.push('已停用');
      if (p.mode === 'none' && !notes.length) notes.push('不限周期');
      row.title = notes.length ? notes.join(' · ') : '';
      if (p.mode !== 'none') {
        state.className = 'tgt-per-state ' + (p.claimed ? 'ok' : 'wait');
        state.textContent = p.claimed ? '已领取' : '未领取';
      } else {
        state.className = 'tgt-per-state mute';
        state.textContent = '每场都跑';
      }
      const sub = document.createElement('i');
      sub.className = 'tgt-per-sub';
      sub.textContent = notes.length ? notes.join(' · ') : (p.label || '');
      row.appendChild(name);
      row.appendChild(state);
      row.appendChild(sub);
      box.appendChild(row);
    });
  }

  /* 手动测试的目标下拉:state 每次变化都会 refresh,所以只在目标名单真的变了
     时才重建选项 —— 否则用户刚选的「只跑 Trae」会被一次状态推送冲掉。 */
  let runTargetSig = null;
  function renderRunTargets(items) {
    const sel = $('runTarget');
    if (!sel) return;
    const list = items || [];
    const sig = list.map((t) => t.id).join(',');
    if (sig === runTargetSig) return;
    runTargetSig = sig;
    const keep = sel.value;
    sel.innerHTML = '';
    const all = document.createElement('option');
    all.value = '';
    all.textContent = '全部目标(按顺序挨个跑)';
    sel.appendChild(all);
    list.forEach((t) => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = (t.name || t.id) + (t.enabled === false ? '(已停用)' : '');
      sel.appendChild(opt);
    });
    sel.value = keep && list.some((t) => t.id === keep) ? keep : '';
  }

  window.PAGES.dashboard = {
    init() {
      $('gotoLogs').addEventListener('click', () => window.APP.switchTab('logs'));

      // 手动测试区:完整流程 / 演练(不点击),可只跑下拉里选中的那一个目标
      const bindRun = (id, dry) => {
        const button = $(id);
        if (!button) return;
        button.addEventListener('click', async () => {
          const sel = $('runTarget');
          const target = sel ? sel.value : '';
          const payload = {};
          if (dry) payload.dry = true;
          if (target) payload.target = target;
          try {
            const res = await window.API.call('run_once', Object.keys(payload).length ? payload : null);
            window.APP.toast((res && res.message) || '已开始', res && res.ok ? 'ok' : 'err');
            if (res && res.ok) window.APP.refreshState();
          } catch (err) {
            window.APP.toast('启动失败:' + err.message, 'err');
          }
        });
      };
      bindRun('runOncePage', false);
      bindRun('runDryPage', true);

      $('setupList').addEventListener('click', async (event) => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.dataset.goto) {
          window.APP.switchTab(button.dataset.goto);
          return;
        }
        if (button.dataset.action === 'master') {
          $('master').checked = true;
          try {
            await window.API.call('set_master', { enabled: true });
            window.APP.toast('总开关已开启', 'ok');
          } catch (err) {
            window.APP.toast('开启失败:' + err.message, 'err');
          }
          window.APP.refreshState();
        }
      });
    },

    refresh(state) {
      const next = state.next_slot;
      $('cardNext').textContent = next ? next.label : '--';
      // 场次可以只排在部分星期,所以这里必须带出日期与星期,
      // 否则「19:00」看起来像今天,实际可能是下周五
      if (!next) {
        $('cardNextSub').textContent = state.slots_enabled ? '已无待执行场次' : '未配置场次';
      } else if (next.is_today) {
        $('cardNextSub').textContent = `今天 ${next.weekday} · ${next.datetime}`;
      } else {
        $('cardNextSub').textContent = `${next.date} ${next.weekday} · ${next.datetime}`;
      }

      // 数字卡片走显式滚动动画(只对纯数字生效),不再靠观察自身文字变化
      // 每个签到目标一行周期状态:周期是「每天 / 每周 / 不限」按目标配置,
      // 全局的「每周一次」已经不存在了。
      renderTargets(state.targets);
      renderRunTargets(state.targets);

      // 「今日场次」只数今天真的会触发的启用场次:场次可只排在部分星期,
      // 未指定星期(每天)或 days 含今天才算
      const pyDay = (new Date().getDay() + 6) % 7;   // JS 周日=0 → Python 周一=0
      const todays = (state.slots || []).filter((s) => s.enabled !== false &&
        (!s.days || !s.days.length || (s.days || []).indexOf(pyDay) >= 0));
      if (window.MOTION) window.MOTION.countTo($('cardSlots'), todays.length);
      else $('cardSlots').textContent = String(todays.length);
      $('cardSlotsSub').textContent = `全部配置 ${state.slots_total || 0} 个`;

      const today = state.today || {};
      const parts = [`✓${today.success || 0}`, `✗${today.failed || 0}`];
      if (today.need_manual) parts.push(`⚠${today.need_manual}`);
      if (today.skipped) parts.push(`↷${today.skipped}`);
      $('cardToday').textContent = (today.total || 0) ? parts.join('  ') : '-';
      const notes = [];
      if (today.not_available) notes.push(today.not_available + ' 场无需领取');
      if (today.skipped) notes.push(today.skipped + ' 场已跳过');
      $('cardTodaySub').textContent = (today.total || 0)
        ? `共 ${today.total} 场${notes.length ? '(' + notes.join('、') + ')' : ''}`
        : '暂无记录';

      $('cardState').textContent = state.running ? '执行中' : (state.master ? '待命' : '已关闭');
      $('cardStateSub').textContent = `总开关:${state.master ? '开启' : '关闭'}${state.dry_run ? ' · 演练模式' : ''}`;

      this.renderSetup(state);
      this.renderRecent(state.recent || []);
    },

    /* 三步上手检查:全部完成后自动收起 */
    renderSetup(state) {
      const panel = $('setupPanel');
      if (!panel) return;
      const flags = {
        key: !!state.has_key,
        slots: (state.slots_enabled || 0) > 0,
        master: !!state.master,
      };
      const items = $('setupList').querySelectorAll('li[data-key]');
      let done = 0;
      items.forEach((item) => {
        const ok = !!flags[item.dataset.key];
        if (ok) done += 1;
        item.classList.toggle('done', ok);
        const button = item.querySelector('button');
        if (button) button.classList.toggle('hidden', ok);
        const chip = item.querySelector('.done-chip');
        if (ok && !chip) {
          const mark = document.createElement('span');
          mark.className = 'chip-chip done-chip';
          mark.textContent = '已完成';
          item.appendChild(mark);
        } else if (!ok && chip) {
          chip.remove();
        }
      });
      $('setupCount').textContent = `${done} / ${items.length}`;
      panel.classList.toggle('hidden', done === items.length);
    },

    renderRecent(items) {
      const tbody = $('recentTable').querySelector('tbody');
      const empty = $('recentEmpty');
      tbody.innerHTML = '';
      empty.classList.toggle('hidden', items.length > 0);
      items.forEach((item) => {
        const info = window.UITL.statusInfo(item.status);
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td class="mono">${window.UITL.esc(window.UITL.dt(item.started_at))}</td>
          <td>${window.UITL.esc(item.slot || '手动')}</td>
          <td>${window.UITL.esc(item.target_name || '--')}</td>
          <td class="${info.cls}">${window.UITL.esc(info.text)}</td>
          <td>${window.UITL.esc(item.summary || '')}</td>
          <td>${item.attempts || 0}</td>
          <td><button class="btn small">回放</button></td>`;
        tr.querySelector('button').addEventListener('click', () => {
          window.APP.switchTab('logs');
          window.PAGES.logs.open(item.id);
        });
        tbody.appendChild(tr);
      });
    },
  };
})();
