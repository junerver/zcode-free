/* 流程页:整体流程的可视化 + 执行时实时高亮 */
(function () {
  const $ = (id) => document.getElementById(id);
  window.PAGES = window.PAGES || {};

  // 流程图节点顺序(视觉流程与 runner 的步骤名一一对应)
  const ORDER = ['trigger', 'open', 'shot1', 'locate', 'click', 'verify', 'done'];
  const STEP_TO_NODE = {
    '打开客户端': 'open',
    '截图': 'shot1',
    '识别按钮': 'locate',
    '点击领取': 'click',
    '按坐标点击': 'click',      // 固定坐标流程:点击一步换成录制好的坐标
    '校验结果': 'verify',
    '收尾': 'done',
  };
  // 只有视觉流程会走的两步:跑固定坐标时把它们压暗,免得被读成"也截了图去识别"
  const VISION_ONLY = ['shot1', 'locate'];
  const END_LABEL = {
    success: '成功', failed: '失败', need_manual: '需人工',
    aborted: '已中止', dry_run: '演练完成',
    not_available: '无需领取',
    skipped: '本周期已领取,跳过',
  };

  const nodes = {};
  let lastAttempt = 0;
  // 只标记"真实走过"的节点:未执行到的步骤保持中性,避免把没跑的点击/校验标成完成
  let visited = new Set();
  let failed = new Set();
  let current = null;
  let visionOnlyDim = false;    // 本次是固定坐标流程

  function resetPath(withTrigger) {
    // 触发节点贯穿整个会话;每次重试只重置"本次尝试"走过的路径
    visited = new Set(withTrigger ? ['trigger'] : []);
    failed = new Set();
    current = null;
    render();
  }

  function render() {
    ORDER.forEach((id) => {
      const el = nodes[id];
      if (!el) return;
      el.classList.toggle('skip', visionOnlyDim && VISION_ONLY.indexOf(id) >= 0);
      el.classList.toggle('fail', failed.has(id));
      el.classList.toggle('active', current === id && !failed.has(id));
      el.classList.toggle('done', visited.has(id) && current !== id && !failed.has(id));
    });
  }

  function applyStep(step, status) {
    const node = STEP_TO_NODE[step];
    if (!node) return;
    if (current && current !== node) visited.add(current);
    current = node;
    if (status === 'fail') failed.add(node);
    render();
  }

  function flash(branch) {
    const el = document.querySelector('.branch[data-branch="' + branch + '"]');
    if (!el) return;
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1600);
  }

  function setPhase(text) {
    const el = $('flowPhase');
    if (el) el.textContent = text;
  }

  // 一次场次可能挨个跑多个目标,这里显示"当前在跑哪个 + 第几个"
  function setTarget(name, index, total) {
    const el = $('flowTarget');
    if (!el) return;
    const label = name || '未运行';
    el.textContent = total > 1 ? label + ' ' + index + '/' + total : label;
    el.classList.toggle('muted', !name);
  }

  window.PAGES.flow = {
    init() {
      ORDER.forEach((id) => {
        nodes[id] = document.querySelector('#flowMain .flow-node[data-node="' + id + '"]');
      });

      EV.on('exec_start', (event) => {
        const ev = event || {};
        visionOnlyDim = ev.flow === 'steps';
        setTarget(ev.target_name, 1, 1);
        if (ev.skipped) {
          // 本周期已领到的跳过场次没有流程可画:直接落结果标签,不亮「执行中」
          resetPath(false);
          setPhase(END_LABEL.skipped);
          return;
        }
        resetPath(true);
        // 相位名来自后端(视觉五步 / 固定坐标四步),流程图只画视觉那条链
        setPhase((ev.dry_run ? '演练中' : '执行中') + ' · ' + ((ev.steps || [])[0] || '打开客户端'));
      });

      EV.on('step_update', (event) => {
        if (!event || !event.step) return;
        applyStep(event.step, event.status);
        const attempt = event.attempt ? ' · 第 ' + event.attempt + ' 次尝试' : '';
        setPhase('执行中' + attempt + ' · ' + event.step);
      });

      EV.on('attempt_update', (event) => {
        if (!event) return;
        if (event.status === 'retry') flash('retry');
        // 新一次尝试:重新开始画路径(触发节点保留)
        if (event.status === 'running' && event.attempt !== lastAttempt) {
          lastAttempt = event.attempt;
          resetPath(true);
          setPhase('执行中 · 第 ' + event.attempt + ' 次尝试');
        }
      });

      EV.on('session_end', (event) => {
        if (!event) return;
        if (current) {
          if (event.status === 'success' || event.status === 'dry_run'
              || event.status === 'not_available') visited.add(current);
          else if (!failed.size) failed.add(current);
        }
        current = null;
        render();
        setPhase((event.target_name ? event.target_name + ':' : '') +
          (END_LABEL[event.status] || event.status || '空闲'));
        if (event.status === 'success') flash('ok');
        else if (event.status === 'need_manual') flash('manual');
        else if (event.status === 'failed') flash('retry');
      });
    },

    async onShow() {
      try {
        const state = await window.API.call('get_state');
        const cur = state && state.current;
        if (cur && cur.step) {
          visionOnlyDim = cur.flow === 'steps';
          resetPath(true);
          applyStep(cur.step, 'ok');
          lastAttempt = cur.attempt || 0;
          setTarget(cur.target_name, cur.target_index || 1, cur.targets_total || 1);
          // 相位表由后端给出(视觉五步 / 固定坐标四步),这里按当前那步显示
          setPhase('执行中' + (lastAttempt ? ' · 第 ' + lastAttempt + ' 次尝试' : '') +
            ' · ' + (cur.step || (cur.steps || [])[0] || '打开客户端'));
        } else if (!current && !visited.size) {
          setTarget('');
          setPhase('空闲');   // 若还画着上次的路径,保留那次的结果标签
        }
      } catch (err) { /* 忽略:仅影响高亮 */ }
    },
  };
})();
