/* 识别页:模型连接 / 识别参数 / 判定词表 / 测试识别与校验(按签到目标) */
(function () {
  const $ = (id) => document.getElementById(id);
  window.PAGES = window.PAGES || {};

  const PRESETS = {
    deepseek: { base_url: 'https://api.deepseek.com', model: 'deepseek-v4-flash-vision-exp' },
    zhipu: { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4v-flash' },
    dashscope: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-vl-plus' },
  };

  // test_verify 回来的判定 → 人话。词表调得对不对,全看这一步的结论
  const VERDICT = {
    success: { text: '领取成功', cls: 'ok', note: '判成,执行器会收尾' },
    claimed: { text: '本周期已领取', cls: 'ok', note: '按成功收尾,不再重试' },
    failed: { text: '明确失败', cls: 'err', note: '命中失败词或失败弹窗' },
    manual: { text: '需要人工处理', cls: 'err', note: '登录页/验证码一类,本场会停在这里' },
    pending: { text: '仍在加载', cls: '', note: '没结论也不算失败,会继续等' },
    retry: { text: '本次未成功', cls: 'err', note: '认不出画面,会重开客户端重试' },
    claim: { text: '可以点击领取', cls: '', note: '定位阶段的结论,不是校验结论' },
    no_card: { text: '画面上没有福利卡片', cls: 'err', note: '复查一次仍不在就按重试处理' },
  };

  let successKeywords = [];
  let claimedKeywords = [];
  let failureKeywords = [];
  let loadingKeywords = [];
  let loading = false;
  let targetOpts = [];   // [{id,name,flow}] —— 测试识别/校验都按选中的目标来
  let chosenTarget = '';

  // 「固定坐标」的目标不跑视觉,测试识别对它只是帮忙看看模型能不能认出按钮
  function flowNote() {
    const hit = targetOpts.filter((t) => t.id === chosenTarget)[0];
    return hit && hit.flow === 'steps' ? ' · 该目标走固定坐标,结果仅供参考' : '';
  }

  async function loadTargets() {
    try {
      const data = await window.API.call('get_targets');
      targetOpts = (data && data.targets) || [];
    } catch (err) { targetOpts = []; }
    const sel = $('vTargetSel');
    sel.innerHTML = targetOpts.length
      ? targetOpts.map((t) => `<option value="${window.UITL.esc(t.id)}">${window.UITL.esc(t.name || t.id)}</option>`).join('')
      : '<option value="">暂无签到目标</option>';
    const keep = targetOpts.filter((t) => t.id === chosenTarget)[0];
    chosenTarget = keep ? keep.id : (targetOpts.length ? targetOpts[0].id : '');
    if (chosenTarget) sel.value = chosenTarget;
  }

  /* 提示词只读区:定位词是全局的,校验词每个目标可以各留一份 */
  async function loadPrompts() {
    try {
      const prompts = await window.API.call('get_prompts', { target: chosenTarget });
      $('locatePromptText').textContent = prompts.locate || '';
      const active = prompts.verify_active || prompts.verify || '';
      $('verifyPromptText').textContent = active;
      const hit = targetOpts.filter((t) => t.id === prompts.target)[0];
      const name = (hit && hit.name) || prompts.target || '未配置目标';
      const which = (prompts.verify_active && prompts.verify &&
        prompts.verify_active !== prompts.verify) ? '用的是该目标自己的校验词' : '用的是全局校验词';
      $('promptTargetNote').textContent = '当前目标:' + name + ' · ' + which + flowNote();
    } catch (err) {
      $('promptTargetNote').textContent = '提示词读取失败:' + err.message;
    }
  }

  /* Key 字段不再回填明文:输入框保持空白,占位符展示「已保存(尾号 xxxx)」;
     用户重新输入则整体替换,留空提交表示不修改(后端约定) */
  function setKeyField(hint) {
    const input = $('vApiKey');
    input.value = '';
    input.placeholder = hint
      ? '已保存(尾号 ' + hint + '),留空表示不修改'
      : 'platform.deepseek.com 创建';
    input.type = 'password';
    $('vToggleKey').textContent = '显示';
  }

  function collectVision() {
    return {
      provider: $('vProvider').value,
      base_url: $('vBaseUrl').value.trim(),
      api_key: $('vApiKey').value.trim(),
      model: $('vModel').value.trim(),
      temperature: Number($('vTemperature').value),
      detail: $('vDetail').value,
      locate_confidence_min: Number($('vMinConf').value),
      timeout_s: Number($('vTimeout').value) || 20,
      humanize_mouse: $('vHumanize').checked,
    };
  }

  async function saveVision(extra) {
    if (loading) return;
    try {
      await window.API.call('save_vision_config', Object.assign({ vision: collectVision() }, extra || {}));
    } catch (err) {
      window.APP.toast('保存失败:' + err.message, 'err');
    }
  }

  function renderChips() {
    renderChipSet('successChips', successKeywords, (list) => {
      successKeywords = list;
      saveVision({ success_keywords: list });
    });
    renderChipSet('claimedChips', claimedKeywords, (list) => {
      claimedKeywords = list;
      saveVision({ claimed_keywords: list });
    });
    renderChipSet('failureChips', failureKeywords, (list) => {
      failureKeywords = list;
      saveVision({ failure_keywords: list });
    });
    renderChipSet('loadingChips', loadingKeywords, (list) => {
      loadingKeywords = list;
      saveVision({ loading_keywords: list });
    });
  }

  function renderChipSet(containerId, list, onChange) {
    const box = $(containerId);
    box.innerHTML = '';
    list.forEach((word, index) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML = `${window.UITL.esc(word)}<button title="删除">×</button>`;
      chip.querySelector('button').addEventListener('click', () => {
        const next = list.slice();
        next.splice(index, 1);
        onChange(next);
        renderChips();
      });
      box.appendChild(chip);
    });
  }

  function bindChipInput(inputId, getList, setList, payloadKey) {
    $(inputId).addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      const value = $(inputId).value.trim();
      if (!value) return;
      const next = getList().concat([value]);
      setList(next);
      saveVision({ [payloadKey]: next });
      $(inputId).value = '';
      renderChips();
    });
  }

  window.PAGES.vision = {
    init() {
      $('vToggleKey').addEventListener('click', () => {
        const input = $('vApiKey');
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        $('vToggleKey').textContent = show ? '隐藏' : '显示';
      });

      // 清除已保存的 Key:输入框留空表示「不修改」,所以删除要走这个显式入口
      $('vClearKey').addEventListener('click', async () => {
        if (!(await window.APP.confirm('确定清除已保存的 API Key 吗?\n清除后到点领取会因缺少 Key 停在识别一步。'))) return;
        try {
          await window.API.call('save_vision_config', { vision: { api_key: null } });
          setKeyField('');
          window.APP.toast('已清除 API Key', 'ok');
        } catch (err) {
          window.APP.toast('清除失败:' + err.message, 'err');
        }
      });

      $('vTemperature').addEventListener('input', () => {
        $('vTemperatureVal').textContent = Number($('vTemperature').value).toFixed(1);
      });
      $('vTemperature').addEventListener('change', () => saveVision());
      $('vMinConf').addEventListener('input', () => {
        $('vMinConfVal').textContent = Number($('vMinConf').value).toFixed(2);
      });
      $('vMinConf').addEventListener('change', () => saveVision());

      ['vBaseUrl', 'vApiKey', 'vModel', 'vTimeout', 'vDetail', 'vHumanize'].forEach((id) => {
        $(id).addEventListener('change', () => saveVision());
      });
      $('vVerifyDelay').addEventListener('change', () =>
        saveVision({ verify_wait_s: Number($('vVerifyDelay').value) || 30 }));

      $('vProvider').addEventListener('change', () => {
        const preset = PRESETS[$('vProvider').value];
        if (preset) {
          $('vBaseUrl').value = preset.base_url;
          $('vModel').value = preset.model;
        }
        saveVision();
      });

      bindChipInput('successInput', () => successKeywords, (list) => { successKeywords = list; }, 'success_keywords');
      bindChipInput('claimedInput', () => claimedKeywords, (list) => { claimedKeywords = list; }, 'claimed_keywords');
      bindChipInput('failureInput', () => failureKeywords, (list) => { failureKeywords = list; }, 'failure_keywords');
      bindChipInput('loadingInput', () => loadingKeywords, (list) => { loadingKeywords = list; }, 'loading_keywords');

      // 换目标 → 校验提示词跟着换(词表是全局的,提示词是每个目标一份)
      $('vTargetSel').addEventListener('change', () => {
        chosenTarget = $('vTargetSel').value;
        loadPrompts();
      });

      $('btnTestConn').addEventListener('click', async () => {
        const box = $('connResult');
        box.classList.remove('hidden', 'ok', 'err');
        box.textContent = '测试中…';
        try {
          const res = await window.API.call('test_vision_connection');
          box.classList.add(res && res.ok ? 'ok' : 'err');
          box.textContent = (res && res.message) || '未知结果';
        } catch (err) {
          box.classList.add('err');
          box.textContent = '测试失败:' + err.message;
        }
      });

      $('btnTestLocate').addEventListener('click', async () => {
        const box = $('locateResult');
        box.classList.remove('hidden');
        const ph = $('locatePlaceholder');
        if (ph) ph.classList.add('hidden');
        $('locateMeta').textContent = '截图中…';
        $('locateJson').textContent = '';
        $('locateImage').removeAttribute('src');
        $('locateBox').classList.add('hidden');
        try {
          const res = await window.API.call('test_locate',
            { target: chosenTarget, source: $('locateSource').value });
          if (res.image) $('locateImage').src = res.image;
          $('locateMeta').textContent = `${targetLabel(res.target)} · ${res.source || ''} · ` +
            `${(res.size || []).join('x')}${flowNote()}`;
          $('locateJson').textContent = JSON.stringify(res.result, null, 2);
          if (res.box) {
            const b = res.box;
            const el = $('locateBox');
            el.style.left = (b[0] * 100) + '%';
            el.style.top = (b[1] * 100) + '%';
            el.style.width = ((b[2] - b[0]) * 100) + '%';
            el.style.height = ((b[3] - b[1]) * 100) + '%';
            el.classList.remove('hidden');
          }
          window.APP.toast(res.ok ? '测试识别完成' : ('识别失败:' + (res.error || '')),
            res.ok ? 'ok' : 'err');
        } catch (err) {
          $('locateMeta').textContent = '测试失败:' + err.message;
        }
      });

      /* 校验自检:走的是所选目标真正生效的那份校验提示词,
         回来的 verdict 就是执行器那一步会做的结论 */
      $('btnTestVerify').addEventListener('click', async () => {
        const box = $('verifyResult');
        box.classList.remove('hidden', 'ok', 'err');
        box.textContent = '截图中并交给模型判定…';
        $('verifyGrid').classList.add('hidden');
        try {
          const res = await window.API.call('test_verify', {
            target: chosenTarget,
            source: $('vVerifySource').value,
            clicked: $('vClicked').checked,
          });
          const info = VERDICT[res.verdict] || { text: String(res.verdict || '无结论'), cls: '', note: '' };
          box.classList.add(res.error ? 'err' : info.cls);
          // 一律用 textContent 拼接,避免把后端文案当 HTML 插进去
          box.textContent = [
            targetLabel(res.target),
            '判定:' + info.text,
            info.note,
            res.error ? '错误:' + res.error : '',
          ].filter(Boolean).join(' · ');
          if (res.image) {
            $('verifyImage').src = res.image;
            $('verifyJson').textContent = JSON.stringify(res.result, null, 2);
            $('verifyGrid').classList.remove('hidden');
          }
        } catch (err) {
          box.classList.add('err');
          box.textContent = '校验测试失败:' + err.message;
        }
      });
    },

    async onShow() {
      loading = true;
      try {
        const data = await window.API.call('get_vision_config');
        const v = data.vision || {};
        $('vProvider').value = v.provider || 'deepseek';
        $('vBaseUrl').value = v.base_url || '';
        setKeyField(v.api_key_hint || '');
        $('vModel').value = v.model || '';
        $('vTemperature').value = v.temperature != null ? v.temperature : 0.2;
        $('vTemperatureVal').textContent = Number($('vTemperature').value).toFixed(1);
        $('vDetail').value = v.detail || 'auto';
        $('vMinConf').value = v.locate_confidence_min != null ? v.locate_confidence_min : 0.7;
        $('vMinConfVal').textContent = Number($('vMinConf').value).toFixed(2);
        $('vTimeout').value = v.timeout_s || 20;
        $('vVerifyDelay').value = data.verify_wait_s != null ? data.verify_wait_s : 30;
        $('vHumanize').checked = !!v.humanize_mouse;
        successKeywords = data.success_keywords || [];
        claimedKeywords = data.claimed_keywords || [];
        failureKeywords = data.failure_keywords || [];
        loadingKeywords = data.loading_keywords || [];
        const prompts = await window.API.call('get_prompts');
        $('locatePromptText').textContent = prompts.locate || '';
        $('verifyPromptText').textContent = prompts.verify || '';
      } catch (err) {
        window.APP.toast('读取识别配置失败:' + err.message, 'err');
      } finally {
        loading = false;
      }
      renderChips();
    },
  };
})();
