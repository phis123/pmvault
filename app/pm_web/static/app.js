/* =============================================================================
 * 账号密码管理 · 浏览器界面（原生 JS，无框架、无构建、无外部依赖）
 *
 * 后端：同源本地 HTTP 服务，接口约定见 server.py。
 * 安全约定：
 *   - 所有来自接口的文本都通过 textContent / createElement 渲染，绝不拼接 innerHTML；
 *   - 不使用 localStorage / sessionStorage 保存任何密码、恢复密钥或同步口令，全部放内存变量；
 *   - 密码明文只出现在「编辑」弹窗内，详情页只显示后端下发的掩码。
 * ========================================================================== */
(function () {
  'use strict';

  var PM = {};

  /* ========================= 1. 全局状态 ========================= */
  var state = {
    locked: true,          // 是否处于锁屏态
    vaultExists: false,    // 保险库文件是否存在（锁屏页决定按钮文案）
    vaultPath: '',         // 保险库路径（只在锁屏页提示）
    entries: [],           // GET /api/entries 返回的条目（password 是掩码）
    query: '',             // 搜索词
    selectedId: null,      // 当前选中条目 id
    summary: null,         // GET /api/state 的结果
    settings: null,        // 设置对象
    editingId: null,       // 正在编辑的条目 id；null 表示新增
    editingEntry: null,    // 正在编辑的条目快照
    revealed: {},          // { entryId: 明文密码 }，仅当前会话内存
    revealTimers: {},      // { entryId: 定时器 }
    lastActivity: Date.now(),
    cooldownTimer: null,
    generateSeq: 0,        // 生成器请求序号，防止旧响应覆盖新预览
    generatorResult: '',
    syncPreviewed: false,  // 是否已做过一次解析预览
    syncConflict: false,   // 预览结果是否冲突（决定是否二次确认）
    syncOutputText: ''     // 导出的同步字符串
  };

  /* ========================= 2. 小工具 ========================= */
  function $(id) { return document.getElementById(id); }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function formatTime(seconds) {
    if (!seconds || seconds <= 0) { return '刚刚'; }
    var d = new Date(seconds * 1000);
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
      ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }

  function pluralCount(n) { return String(n); }

  /** 弹出右下角提示条（纯 textContent，无 HTML 解析）。 */
  function toast(message, kind, ms) {
    var wrap = $('toast-wrap');
    if (!wrap) { return; }
    var el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = message;
    wrap.appendChild(el);
    window.setTimeout(function () {
      if (el.parentNode) { el.parentNode.removeChild(el); }
    }, ms || (kind === 'err' ? 5200 : 2800));
  }

  function showError(id, message) {
    var el = $(id);
    if (el) { el.textContent = message || ''; }
  }

  function clearChildren(node) {
    while (node && node.firstChild) { node.removeChild(node.firstChild); }
  }

  /** 建元素的小助手：文本一律走 textContent。 */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function emptyHint(text) {
    var hint = el('div', 'hint', text);
    return hint;
  }

  function isDialogSupported() {
    return typeof HTMLDialogElement !== 'undefined' &&
      typeof document.createElement('dialog').showModal === 'function';
  }

  function openDialog(id) {
    var dlg = $(id);
    if (!dlg) { return; }
    if (isDialogSupported()) {
      if (!dlg.open) { dlg.showModal(); }
    } else {
      dlg.setAttribute('open', 'open');
    }
  }

  function closeDialog(id) {
    var dlg = $(id);
    if (!dlg) { return; }
    if (isDialogSupported()) {
      if (dlg.open) { dlg.close(); }
    } else {
      dlg.removeAttribute('open');
    }
  }

  function anyDialogOpen() {
    var list = document.querySelectorAll('dialog[open]');
    return list.length > 0;
  }

  /* ========================= 3. 接口层 ========================= */

  /** 统一解析响应：401 且 locked 时直接回锁屏页；其余抛中文错误。 */
  function handleResponse(res) {
    return res.json().catch(function () { return {}; }).then(function (data) {
      if (res.ok) { return data; }
      if (res.status === 401 && data && data.locked) {
        goLocked(data.error || '保险库未解锁');
      }
      var err = new Error((data && data.error) || ('请求失败（HTTP ' + res.status + '）'));
      err.status = res.status;
      err.data = data || {};
      throw err;
    });
  }

  function apiCall(path, opts) {
    opts = opts || {};
    var init = { credentials: 'same-origin', cache: 'no-store' };
    if (opts.method) { init.method = opts.method; }
    if (opts.body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(handleResponse);
  }

  function apiGet(path) { return apiCall(path, {}); }
  function apiSend(path, method, body) { return apiCall(path, { method: method, body: body }); }

  /* ========================= 4. 锁屏页 ========================= */

  /** 回到锁屏页：清空内存中的条目、明文、选中项。 */
  function goLocked(message) {
    state.locked = true;
    state.entries = [];
    state.selectedId = null;
    state.editingId = null;
    state.editingEntry = null;
    state.revealed = {};
    Object.keys(state.revealTimers).forEach(function (key) {
      window.clearTimeout(state.revealTimers[key]);
    });
    state.revealTimers = {};
    state.syncPreviewed = false;
    state.syncConflict = false;
    closeAllDialogs();
    $('vault-view').classList.add('hidden');
    $('lock-screen').classList.remove('hidden');
    $('master-password').value = '';
    $('recovery-key').value = '';
    $('recovery-panel').classList.add('hidden');
    stopCooldown();
    if (message) { toast(message, 'warn'); }
    refreshLockInfo();
    window.setTimeout(function () { $('master-password').focus(); }, 30);
  }

  function closeAllDialogs() {
    ['dlg-entry', 'dlg-generator', 'dlg-sync', 'dlg-settings', 'dlg-recovery'].forEach(function (id) {
      closeDialog(id);
    });
  }

  /** 查询保险库是否存在，决定锁屏按钮文案。未解锁时这个接口不需要会话。 */
  function refreshLockInfo() {
    return apiGet('/api/entries').then(function (data) {
      if (data && data.locked) {
        state.vaultExists = !!data.vault_exists;
        state.vaultPath = data.vault_path || '';
      } else if (data && data.locked === false) {
        state.vaultExists = true;
      }
      applyLockCopy();
    }).catch(function (err) {
      // 会话尚未建立时可能 401，重试一次（先 GET 首页会下发 Cookie）
      if (err && err.status === 401) {
        return fetch('/', { credentials: 'same-origin', cache: 'no-store' })
          .then(function () { return apiGet('/api/entries'); })
          .then(function (data) {
            if (data && data.locked) {
              state.vaultExists = !!data.vault_exists;
              state.vaultPath = data.vault_path || '';
            }
            applyLockCopy();
          })
          .catch(function () { /* 忽略，保持默认文案 */ });
      }
      showError('unlock-error', err.message);
    });
  }

  function applyLockCopy() {
    $('btn-unlock').textContent = state.vaultExists ? '解锁' : '创建并解锁';
    $('lock-subtitle').textContent = state.vaultExists
      ? '请输入主密码以解锁本地保险库'
      : '还没有保险库，请设置一个至少 8 位的主密码来创建';
    $('vault-path-hint').textContent = state.vaultPath ? ('保险库文件：' + state.vaultPath.split(/[\\/]/).pop()) : '';
    $('toggle-master').textContent = '显示';
    $('master-password').type = 'password';
  }

  /** 429 倒计时：按钮禁用并实时显示剩余秒数。 */
  function startCooldown(seconds) {
    stopCooldown();
    var remain = Math.ceil(seconds);
    var btn = $('btn-unlock');
    var info = $('unlock-cooldown');
    btn.disabled = true;
    function tick() {
      if (remain <= 0) {
        stopCooldown();
        return;
      }
      info.textContent = '尝试过于频繁，请等待 ' + remain + ' 秒后再试';
      remain -= 1;
      state.cooldownTimer = window.setTimeout(tick, 1000);
    }
    tick();
  }

  function stopCooldown() {
    if (state.cooldownTimer) {
      window.clearTimeout(state.cooldownTimer);
      state.cooldownTimer = null;
    }
    $('unlock-cooldown').textContent = '';
    $('btn-unlock').disabled = false;
  }

  /** 解锁按钮 / 恢复密钥进度的统一入口。 */
  function doUnlock(mode, payload, opts) {
    opts = opts || {};
    var errorId = opts.errorId || 'unlock-error';
    showError('unlock-error', '');
    showError('unlock-cooldown', '');
    return apiSend('/api/unlock', 'POST', payload).then(function (data) {
      stopCooldown();
      onUnlocked(mode, data);
      return data;
    }).catch(function (err) {
      if (err.status === 429) {
        var secs = (err.data && err.data.cooldown) || 0;
        startCooldown(secs || 5);
        showError(errorId, err.message);
      } else if (err.status === 409 && mode === 'create') {
        // 保险库已存在且未确认覆盖
        var ok = window.confirm(err.message + '\n\n继续将覆盖现有保险库（原文件会保留为 .bak）。确定要覆盖吗？');
        if (ok) {
          return apiSend('/api/unlock', 'POST', {
            mode: 'create', password: payload.password, overwrite: true
          }).then(function (data) { onUnlocked(mode, data); return data; })
            .catch(function (e2) { showError(errorId, e2.message); });
        }
      } else {
        showError(errorId, err.message);
      }
      return null;
    });
  }

  function onUnlocked(mode, data) {
    state.locked = false;
    $('lock-screen').classList.add('hidden');
    $('vault-view').classList.remove('hidden');
    $('master-password').value = '';
    $('recovery-key').value = '';

    // 首次创建：强制抄写恢复密钥
    if (mode === 'create' && data && data.recovery_key) {
      showRecoveryDialog(data.recovery_key, '这是你新建保险库的恢复密钥，是主密码遗忘时唯一的找回方式，请立即抄写并离线保存。');
    }
    // 用恢复密钥解锁：提醒改主密码（改主密码需要旧主密码，只能给入口和提醒）
    if (mode === 'recovery' && data && data.want_change_master) {
      toast('已用恢复密钥解锁，建议尽快在「设置 → 修改主密码」里更换主密码', 'warn', 7000);
    }
    reloadAll().then(function () {
      $('search').focus();
    });
  }

  /* ========================= 5. 数据加载与渲染 ========================= */

  function reloadAll() {
    return Promise.all([loadEntries(), loadState()]).then(function () {
      renderList();
      renderDetail();
    }).catch(function (err) {
      toast(err.message, 'err');
    });
  }

  function loadEntries() {
    var path = '/api/entries';
    if (state.query) { path += '?q=' + encodeURIComponent(state.query); }
    return apiGet(path).then(function (data) {
      if (data && data.locked) {
        goLocked('保险库已锁定');
        return;
      }
      state.entries = (data && data.entries) || [];
      // 选中项如果还在列表里就保持，否则退回第一条
      var ids = state.entries.map(function (item) { return item.id; });
      if (state.selectedId && ids.indexOf(state.selectedId) === -1) {
        state.selectedId = null;
      }
      if (!state.selectedId && state.entries.length) {
        state.selectedId = state.entries[0].id;
      }
    });
  }

  function loadState() {
    return apiGet('/api/state').then(function (data) {
      state.summary = data || null;
      if (data && data.settings) {
        state.settings = data.settings;
        applyGeneratorDefaults(state.settings.generator || {});
      }
      renderStatusBar();
    });
  }

  function renderStatusBar() {
    var s = state.summary;
    if (!s) { return; }
    $('st-count').textContent = '条目数 ' + pluralCount(s.count);
    $('st-revision').textContent = '版本 r' + s.revision;
    $('st-kdf').textContent = 'KDF ' + (s.kdf || '未知');
    var weak = $('st-weak');
    weak.textContent = '弱密码 ' + pluralCount(s.weak || 0);
    weak.className = (s.weak > 0) ? 'warn-num' : '';
    var old = $('st-old');
    old.textContent = '过期 ' + pluralCount(s.old || 0);
    old.className = (s.old > 0) ? 'warn-num' : '';
  }

  function entryById(id) {
    for (var i = 0; i < state.entries.length; i += 1) {
      if (state.entries[i].id === id) { return state.entries[i]; }
    }
    return null;
  }

  /** 标签列表渲染（textContent）。 */
  function renderTags(tags) {
    var box = el('div', 'tags');
    (tags || []).forEach(function (tag) {
      box.appendChild(el('span', 'tag', tag));
    });
    return box;
  }

  /** 弱/重复/过期小标记。 */
  function renderMarks(entry) {
    var box = el('div', 'marks');
    var strength = entry.strength || {};
    if (typeof strength.score === 'number' && strength.score <= 1) {
      box.appendChild(el('span', 'mark mark-weak', '弱'));
    }
    if (entry.duplicate) {
      box.appendChild(el('span', 'mark mark-dup', '重复'));
    }
    if (entry.old) {
      box.appendChild(el('span', 'mark mark-old', '过期'));
    }
    return box;
  }

  function renderList() {
    var list = $('entry-list');
    clearChildren(list);
    var entries = state.entries;

    if (!entries.length) {
      var li = el('li');
      var box = el('div', 'empty-state');
      box.appendChild(el('div', 'big', '🔒'));
      box.appendChild(el('div', null, state.query ? '没有匹配「' + state.query + '」的条目' : '还没有任何条目'));
      if (!state.query) {
        box.appendChild(el('div', 'hint', '点右上角「+ 新增」添加第一条'));
      }
      li.appendChild(box);
      list.appendChild(li);
      $('list-summary').textContent = state.query ? '匹配 0 条' : '共 0 条';
      return;
    }

    entries.forEach(function (entry) {
      var li = el('li', 'entry-item' + (entry.id === state.selectedId ? ' selected' : ''));
      li.setAttribute('data-id', entry.id);
      li.appendChild(el('div', 'name', entry.name || '(未命名)'));

      var line2 = el('div', 'line2');
      line2.appendChild(el('span', 'user', entry.username || '（无用户名）'));
      line2.appendChild(renderMarks(entry));
      li.appendChild(line2);

      if (entry.tags && entry.tags.length) {
        li.appendChild(renderTags(entry.tags));
      }
      li.addEventListener('click', function () {
        state.selectedId = entry.id;
        renderList();
        renderDetail();
      });
      list.appendChild(li);
    });

    $('list-summary').textContent = state.query
      ? ('匹配 ' + entries.length + ' 条')
      : ('共 ' + entries.length + ' 条');
  }

  /* ---------- 详情面板 ---------- */

  function strengthColor(score) {
    if (score <= 1) { return '#c8342f'; }
    if (score === 2) { return '#d68910'; }
    if (score === 3) { return '#2f6fed'; }
    return '#1f8a4c';
  }

  function strengthWidth(score) {
    var map = { 0: 12, 1: 30, 2: 55, 3: 80, 4: 100 };
    return map[score] !== undefined ? map[score] : 10;
  }

  function renderStrength(entry) {
    var strength = entry.strength || { score: 0, label: '未知', entropy: 0 };
    var block = el('div', 'detail-block');
    block.appendChild(el('div', 'k', '密码强度'));

    var row = el('div', 'strength-row');
    var bar = el('div', 'strength-bar');
    var fill = el('i');
    fill.style.width = strengthWidth(strength.score) + '%';
    fill.style.background = strengthColor(strength.score);
    bar.appendChild(fill);
    row.appendChild(bar);

    var note = el('span', 'strength-note', (strength.label || '未知') +
      '（score ' + strength.score + '/4 · 熵 ' + Math.round(strength.entropy || 0) + ' bit）');
    row.appendChild(note);
    block.appendChild(row);

    var age = el('div', 'hint', '密码已使用 ' + entry.age_days + ' 天' +
      (entry.password_updated_at ? '（上次修改 ' + formatTime(entry.password_updated_at) + '）' : ''));
    if (entry.old) { age.style.color = '#b4761a'; }
    block.appendChild(age);
    return block;
  }

  /** 带「复制」按钮的一行；secret 为真时默认掩码显示，点击「显示」后才放明文。 */
  function renderCopyRow(labelText, value, opts) {
    opts = opts || {};
    var block = el('div', 'detail-block');
    block.appendChild(el('div', 'k', labelText));

    var row = el('div', 'copy-row');
    var val = el('div', 'val');
    var shown = false;
    var real = value === undefined || value === null ? '' : String(value);

    function paint() {
      clearChildren(val);
      val.className = 'val';
      if (opts.secret) {
        if (shown && real) {
          val.textContent = real;
          val.classList.add('mono');
        } else {
          val.textContent = real ? '●●●●●●●●●●' : '（空）';
          val.classList.add('masked');
        }
      } else {
        val.textContent = real || '（空）';
      }
    }
    paint();
    row.appendChild(val);

    if (opts.secret && real) {
      var toggle = el('button', 'btn btn-sm', '显示');
      toggle.type = 'button';
      var timer = null;
      toggle.addEventListener('click', function () {
        shown = !shown;
        if (shown) {
          toggle.textContent = '隐藏';
          paint();
          var seconds = (state.settings && state.settings.reveal_seconds) || 20;
          if (seconds > 0) {
            timer = window.setTimeout(function () {
              shown = false;
              toggle.textContent = '显示';
              paint();
            }, seconds * 1000);
          }
        } else {
          if (timer) { window.clearTimeout(timer); }
          toggle.textContent = '显示';
          paint();
        }
      });
      row.appendChild(toggle);
    }

    if (opts.copy && real) {
      var copyBtn = el('button', 'btn btn-sm', opts.copyLabel || '复制');
      copyBtn.type = 'button';
      copyBtn.addEventListener('click', function () {
        copyToClipboard(real, opts.secret ? '密码' : (opts.copyLabel || '内容'));
      });
      row.appendChild(copyBtn);
    }
    block.appendChild(row);
    return block;
  }

  function renderDetail() {
    var pane = $('detail-pane');
    clearChildren(pane);

    if (!state.selectedId) {
      var box = el('div', 'empty-state');
      box.appendChild(el('div', 'big', '🗝️'));
      box.appendChild(el('div', null, state.entries.length ? '从左侧选择一条记录' : '还没有条目，点「+ 新增」开始'));
      box.appendChild(el('div', 'hint', '快捷键：Ctrl+F 搜索 · Ctrl+N 新增 · Ctrl+L 锁定'));
      pane.appendChild(box);
      return;
    }

    var entry = entryById(state.selectedId);
    if (!entry) {
      pane.appendChild(el('div', 'empty-state', '条目不存在，可能已被删除'));
      return;
    }

    var card = el('div', 'detail-card');

    // 标题 + 操作
    var head = el('div', 'detail-head');
    head.appendChild(el('h2', null, entry.name || '(未命名)'));
    var actions = el('div', 'detail-actions');
    var editBtn = el('button', 'btn', '编辑');
    editBtn.type = 'button';
    editBtn.addEventListener('click', function () { openEntryDialog(entry.id); });
    var delBtn = el('button', 'btn btn-danger', '删除');
    delBtn.type = 'button';
    delBtn.addEventListener('click', function () { deleteEntry(entry); });
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);
    head.appendChild(actions);
    card.appendChild(head);

    // 用户名（可复制）
    card.appendChild(renderCopyRow('用户名', entry.username, { copy: true, copyLabel: '复制用户名' }));

    // 密码：后端下发的掩码；要看真密码请用「编辑」
    var pwBlock = el('div', 'detail-block');
    pwBlock.appendChild(el('div', 'k', '密码'));
    var pwRow = el('div', 'copy-row');
    var pwVal = el('div', 'val masked', entry.password || '●●●●●●●●');
    pwRow.appendChild(pwVal);
    var hint = el('span', 'hint', '出于安全考虑，这里只显示掩码；查看或修改请点「编辑」');
    pwRow.appendChild(hint);
    pwBlock.appendChild(pwRow);
    card.appendChild(pwBlock);

    // 强度
    card.appendChild(renderStrength(entry));

    // 网址
    var urlBlock = el('div', 'detail-block');
    urlBlock.appendChild(el('div', 'k', '网址'));
    if (entry.url) {
      if (/^https?:\/\//i.test(entry.url)) {
        var link = el('a', 'url-link');
        link.href = entry.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = entry.url;
        urlBlock.appendChild(link);
      } else {
        urlBlock.appendChild(el('div', null, entry.url));
      }
    } else {
      urlBlock.appendChild(el('div', 'hint', '（空）'));
    }
    card.appendChild(urlBlock);

    // 标签
    var tagBlock = el('div', 'detail-block');
    tagBlock.appendChild(el('div', 'k', '标签'));
    if (entry.tags && entry.tags.length) {
      tagBlock.appendChild(renderTags(entry.tags));
    } else {
      tagBlock.appendChild(el('div', 'hint', '（无标签）'));
    }
    card.appendChild(tagBlock);

    // 备注
    var noteBlock = el('div', 'detail-block');
    noteBlock.appendChild(el('div', 'k', '备注'));
    if (entry.notes) {
      noteBlock.appendChild(el('div', 'notes-box', entry.notes));
    } else {
      noteBlock.appendChild(el('div', 'hint', '（无备注）'));
    }
    card.appendChild(noteBlock);

    // 自定义字段
    if (entry.custom_fields && entry.custom_fields.length) {
      var cfBlock = el('div', 'detail-block');
      cfBlock.appendChild(el('div', 'k', '自定义字段'));
      var list = el('div', 'cf-list');
      entry.custom_fields.forEach(function (field) {
        var label = field && (field.label || field.name || field.key) ? (field.label || field.name || field.key) : '字段';
        var value = field && field.value !== undefined && field.value !== null ? String(field.value) : '';
        list.appendChild(renderCopyRow(label, value, {
          secret: !!(field && field.secret),
          copy: true,
          copyLabel: '复制'
        }));
      });
      cfBlock.appendChild(list);
      card.appendChild(cfBlock);
    }

    // 元信息
    var meta = el('div', 'detail-block');
    meta.appendChild(el('div', 'k', '时间'));
    meta.appendChild(el('div', 'hint', '创建 ' + formatTime(entry.created_at) +
      ' · 更新 ' + formatTime(entry.updated_at) +
      ' · 密码更新 ' + formatTime(entry.password_updated_at)));
    card.appendChild(meta);

    pane.appendChild(card);
  }

  /* ========================= 6. 剪贴板 ========================= */

  /**
   * 复制文本：优先 navigator.clipboard，失败时退回隐藏 textarea。
   * 复制密码后按 settings.clipboard_clear_seconds 定时尝试清空剪贴板（失败静默忽略）。
   */
  function copyToClipboard(text, what) {
    var seconds = (state.settings && state.settings.clipboard_clear_seconds);
    if (seconds === undefined || seconds === null) { seconds = 20; }

    function done(ok) {
      if (!ok) {
        toast('复制失败，请手动选中复制', 'err');
        return;
      }
      if (what === '密码') {
        toast('已复制，' + seconds + ' 秒后自动清空', 'ok');
      } else {
        toast('已复制' + (what ? '（' + what + '）' : ''), 'ok');
      }
      if (Number(seconds) > 0) {
        window.setTimeout(function () { clearClipboard(); }, Number(seconds) * 1000);
      }
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); })
        .catch(function () { done(legacyCopy(text)); });
      return;
    }
    done(legacyCopy(text));
  }

  function legacyCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }

  /** 清空剪贴板：失败就静默忽略（浏览器可能要求页面可见或用户手势）。 */
  function clearClipboard() {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText('').catch(function () { /* 忽略 */ });
      }
    } catch (e) { /* 忽略 */ }
  }

  /* ========================= 7. 新增 / 编辑弹窗 ========================= */

  function addCustomFieldRow(label, value, secret) {
    var row = el('div', 'cf-row');
    var labelInput = el('input');
    labelInput.type = 'text';
    labelInput.placeholder = '字段名';
    labelInput.value = label || '';
    var valueInput = el('input');
    valueInput.type = 'text';
    valueInput.placeholder = '字段值';
    valueInput.value = value || '';
    valueInput.spellcheck = false;

    var hideLabel = el('label', 'inline-check cf-hide');
    var hideBox = el('input');
    hideBox.type = 'checkbox';
    hideBox.checked = !!secret;
    hideLabel.appendChild(hideBox);
    hideLabel.appendChild(el('span', null, '隐藏'));

    var del = el('button', 'btn btn-sm btn-danger', '删除');
    del.type = 'button';
    del.addEventListener('click', function () {
      row.parentNode.removeChild(row);
    });

    row.appendChild(labelInput);
    row.appendChild(valueInput);
    row.appendChild(hideLabel);
    row.appendChild(del);
    // 供提交时读取
    row._labelInput = labelInput;
    row._valueInput = valueInput;
    row._secretBox = hideBox;
    $('custom-fields').appendChild(row);
  }

  function readCustomFields() {
    var rows = $('custom-fields').querySelectorAll('.cf-row');
    var fields = [];
    Array.prototype.forEach.call(rows, function (row) {
      var label = row._labelInput.value.trim();
      var value = row._valueInput.value;
      if (!label && !value) { return; }
      fields.push({ label: label || '字段', value: value, secret: !!row._secretBox.checked });
    });
    return fields;
  }

  /** 打开新增 / 编辑弹窗。编辑时向后端取一次明文密码填入密码框。 */
  function openEntryDialog(entryId) {
    state.editingId = entryId || null;
    state.editingEntry = entryId ? entryById(entryId) : null;
    showError('entry-error', '');

    var entry = state.editingEntry;
    if (entry) {
      $('entry-dlg-title').textContent = '编辑条目 · ' + (entry.name || '');
      $('f-name').value = entry.name || '';
      $('f-username').value = entry.username || '';
      $('f-password').value = '';
      $('f-password').placeholder = '留空表示不修改密码；要改密码就直接输入或点「生成」';
      $('f-password-hint').textContent = '正在读取当前密码…';
      $('f-url').value = entry.url || '';
      $('f-tags').value = (entry.tags || []).join(', ');
      $('f-notes').value = entry.notes || '';
      clearChildren($('custom-fields'));
      (entry.custom_fields || []).forEach(function (field) {
        var label = field && (field.label || field.name || field.key) ? (field.label || field.name || field.key) : '';
        addCustomFieldRow(label, field ? field.value : '', !!(field && field.secret));
      });
      // 取明文密码：先尝试条目内已缓存的，否则用 reveal 拉一次（只放内存）
      if (state.revealed[entry.id]) {
        $('f-password').value = state.revealed[entry.id];
        $('f-password-hint').textContent = '当前密码已载入（仅本次会话内存中）';
      } else {
        apiSend('/api/entries/' + encodeURIComponent(entry.id), 'PATCH', { reveal: true })
          .then(function (data) {
            var revealed = data && data.entry ? data.entry.password : '';
            state.revealed[entry.id] = revealed || '';
            if (state.editingId === entry.id) {
              $('f-password').value = revealed || '';
              $('f-password-hint').textContent = '当前密码已载入（仅本次会话内存中）';
            }
            // 用返回值刷新列表里的强度等信息（password 字段仍是掩码以外的真实值，只在内存用）
            if (data && data.entry) {
              var local = entryById(entry.id);
              if (local) {
                local.strength = data.entry.strength;
                local.password_updated_at = data.entry.password_updated_at;
                local.age_days = data.entry.age_days;
              }
            }
          })
          .catch(function (err) {
            $('f-password-hint').textContent = '读取当前密码失败：' + err.message + '（可直接输入新密码）';
          });
      }
    } else {
      $('entry-dlg-title').textContent = '新增条目';
      $('f-name').value = '';
      $('f-username').value = '';
      $('f-password').value = '';
      $('f-password').placeholder = '留空表示不修改密码（编辑时）';
      $('f-password-hint').textContent = '建议点「生成」用随机强密码';
      $('f-url').value = '';
      $('f-tags').value = '';
      $('f-notes').value = '';
      clearChildren($('custom-fields'));
    }

    openDialog('dlg-entry');
    window.setTimeout(function () { $('f-name').focus(); }, 30);
  }

  function parseTags(text) {
    return String(text || '')
      .split(/[,，;；]/)
      .map(function (item) { return item.trim(); })
      .filter(function (item, index, arr) { return item && arr.indexOf(item) === index; });
  }

  function saveEntry() {
    showError('entry-error', '');
    var name = $('f-name').value.trim();
    if (!name) {
      showError('entry-error', '名称必填');
      $('f-name').focus();
      return;
    }
    var body = {
      name: name,
      username: $('f-username').value,
      url: $('f-url').value.trim(),
      notes: $('f-notes').value,
      tags: parseTags($('f-tags').value),
      custom_fields: readCustomFields()
    };
    var password = $('f-password').value;
    var isEdit = !!state.editingId;
    if (password || !isEdit) {
      body.password = password;
    }

    var btn = $('btn-save-entry');
    btn.disabled = true;
    var request = isEdit
      ? apiSend('/api/entries/' + encodeURIComponent(state.editingId), 'PATCH', body)
      : apiSend('/api/entries', 'POST', body);

    request.then(function (data) {
      if (isEdit && data && data.entry) {
        state.revealed[data.entry.id] = password || state.revealed[data.entry.id] || '';
      }
      closeDialog('dlg-entry');
      toast(isEdit ? '已保存修改' : '已新增条目', 'ok');
      state.selectedId = (data && data.entry && data.entry.id) || state.selectedId;
      return reloadAll();
    }).catch(function (err) {
      showError('entry-error', err.message);
    }).then(function () {
      btn.disabled = false;
    });
  }

  function deleteEntry(entry) {
    var ok = window.confirm('确定删除「' + (entry.name || '该条目') + '」吗？此操作不可撤销。');
    if (!ok) { return; }
    apiSend('/api/entries/' + encodeURIComponent(entry.id), 'DELETE', undefined)
      .then(function () {
        if (state.selectedId === entry.id) { state.selectedId = null; }
        delete state.revealed[entry.id];
        toast('已删除', 'ok');
        return reloadAll();
      })
      .catch(function (err) { toast(err.message, 'err'); });
  }

  /* ========================= 8. 密码生成器 ========================= */

  var generatorDefaults = {};

  function applyGeneratorDefaults(gen) {
    generatorDefaults = gen || {};
    if (!document.querySelector('dialog[open]')) { return; }
  }

  function openGeneratorDialog() {
    var gen = generatorDefaults || {};
    if (typeof gen.length === 'number') { $('gen-length').value = gen.length; }
    if (typeof gen.lower === 'boolean') { $('gen-lower').checked = gen.lower; }
    if (typeof gen.upper === 'boolean') { $('gen-upper').checked = gen.upper; }
    if (typeof gen.digits === 'boolean') { $('gen-digits').checked = gen.digits; }
    if (typeof gen.symbols === 'boolean') { $('gen-symbols').checked = gen.symbols; }
    if (typeof gen.exclude_confusing === 'boolean') { $('gen-exclude').checked = gen.exclude_confusing; }
    $('gen-length-value').textContent = $('gen-length').value;
    showError('gen-error', '');
    openDialog('dlg-generator');
    requestGeneratedPassword();
  }

  function generatorOptions() {
    return {
      length: Number($('gen-length').value) || 20,
      lower: $('gen-lower').checked,
      upper: $('gen-upper').checked,
      digits: $('gen-digits').checked,
      symbols: $('gen-symbols').checked,
      exclude_confusing: $('gen-exclude').checked
    };
  }

  function requestGeneratedPassword() {
    var options = generatorOptions();
    var seq = state.generateSeq + 1;
    state.generateSeq = seq;
    showError('gen-error', '');
    apiSend('/api/generate', 'POST', options).then(function (data) {
      if (seq !== state.generateSeq) { return; }   // 丢弃过期响应
      state.generatorResult = (data && data.password) || '';
      $('gen-preview').textContent = state.generatorResult || '—';
      var strength = data && data.strength;
      $('gen-strength').textContent = strength
        ? ('强度：' + strength.label + '（score ' + strength.score + '/4 · 熵 ' + Math.round(strength.entropy) + ' bit）')
        : '';
    }).catch(function (err) {
      if (seq !== state.generateSeq) { return; }
      $('gen-preview').textContent = '生成失败';
      showError('gen-error', err.message);
    });
  }

  /** 把生成结果填回条目表单的密码框。 */
  function useGeneratedPassword() {
    if (!state.generatorResult) {
      showError('gen-error', '还没有生成结果');
      return;
    }
    $('f-password').value = state.generatorResult;
    $('f-password-hint').textContent = '已填入生成的密码，保存后生效';
    closeDialog('dlg-generator');
    showError('entry-error', '');
  }

  /* ========================= 9. 同步弹窗 ========================= */

  function loadSyncEstimate() {
    apiGet('/api/sync/estimate').then(function (data) {
      var box = $('sync-estimate');
      clearChildren(box);
      var estimate = (data && data.estimate) || {};
      box.appendChild(el('span', null, '本机条目 ' + data.count + ' 条'));
      box.appendChild(el('span', null, '版本 r' + data.revision));
      box.appendChild(el('span', null, '设备 ' + data.device));
      box.appendChild(el('span', null, '预计 ' + estimate.bytes + ' 字节 / ' + estimate.chunks + ' 个分片'));
    }).catch(function (err) {
      $('sync-estimate').textContent = '读取同步信息失败：' + err.message;
    });
  }

  function openSyncDialog() {
    showError('sync-error', '');
    state.syncPreviewed = false;
    state.syncConflict = false;
    state.syncOutputText = '';
    $('sync-output').value = '';
    $('sync-input').value = '';
    $('sync-passphrase').value = '';
    $('sync-passphrase').type = 'password';
    $('btn-toggle-sync-pass').textContent = '显示';
    $('sync-pack-hint').textContent = '';
    $('btn-sync-apply').classList.add('hidden');
    $('sync-preview-box').classList.add('hidden');
    $('sync-conflict-hint').classList.add('hidden');
    clearChildren($('sync-preview-meta'));
    clearChildren($('sync-preview-list'));
    openDialog('dlg-sync');
    loadSyncEstimate();
  }

  function syncPassphrase() {
    var value = $('sync-passphrase').value;
    if (!value || value.length < 8) {
      showError('sync-error', '同步口令至少 8 位');
      return null;
    }
    return value;
  }

  function packSync() {
    showError('sync-error', '');
    var passphrase = syncPassphrase();
    if (!passphrase) { return; }
    apiSend('/api/sync/pack', 'POST', { passphrase: passphrase }).then(function (data) {
      var lines = (data && data.lines) || [];
      state.syncOutputText = lines.join('\n');
      $('sync-output').value = state.syncOutputText;
      $('sync-pack-hint').textContent = '共 ' + ((data && data.count) || lines.length) + ' 个分片，请全部复制';
    }).catch(function (err) {
      showError('sync-error', err.message);
    });
  }

  /** 解析预览：先不带 apply 调一次；conflict 时要求二次确认。 */
  function previewSync(forceApply) {
    showError('sync-error', '');
    var passphrase = syncPassphrase();
    if (!passphrase) { return; }
    var content = $('sync-input').value;
    if (!content.trim()) {
      showError('sync-error', '请先粘贴同步字符串');
      return;
    }
    var body = { passphrase: passphrase, content: content };
    var wantApply = !!forceApply;

    if (wantApply) {
      if (!state.syncPreviewed) {
        showError('sync-error', '请先做一次「解析预览」再应用');
        return;
      }
      if (state.syncConflict) {
        var ok = window.confirm('检测到冲突：远端与本机都有各自的修改（本机 r' +
          (state.summary ? state.summary.revision : '?') + '）。\n\n继续应用会用远端数据整体覆盖本机条目，确定继续吗？');
        if (!ok) { return; }
      } else {
        var ok2 = window.confirm('应用后会用它覆盖本机条目，确定继续吗？');
        if (!ok2) { return; }
      }
      body.apply = true;
    }

    $('btn-sync-preview').disabled = true;
    $('btn-sync-apply').disabled = true;
    apiSend('/api/sync/unpack', 'POST', body).then(function (data) {
      renderSyncPreview(data);
      if (body.apply) {
        toast('已应用远端数据（' + data.count + ' 条）', 'ok');
        state.revealed = {};
        return reloadAll();
      }
      state.syncPreviewed = true;
      $('btn-sync-apply').classList.remove('hidden');
      return null;
    }).catch(function (err) {
      // 缺分片等情况后端返回 4xx 中文错误，原样展示
      showError('sync-error', err.message);
    }).then(function () {
      $('btn-sync-preview').disabled = false;
      $('btn-sync-apply').disabled = false;
    });
  }

  function renderSyncPreview(data) {
    state.syncConflict = !!data.conflict;
    $('sync-preview-box').classList.remove('hidden');

    var meta = $('sync-preview-meta');
    clearChildren(meta);
    meta.appendChild(el('span', null, '远端版本 r' + data.revision));
    meta.appendChild(el('span', null, '本机版本 r' + data.local_revision));
    meta.appendChild(el('span', null, '远端设备 ' + (data.device || '未知')));
    meta.appendChild(el('span', null, '远端条目 ' + data.count + ' 条'));
    meta.appendChild(el('span', null, '判定：' + decisionText(data.decision)));

    var list = $('sync-preview-list');
    clearChildren(list);
    var entries = data.entries || [];
    if (!entries.length) {
      list.appendChild(el('div', 'row-item', '远端没有条目（应用后会清空本机列表）'));
    }
    entries.slice(0, 40).forEach(function (entry) {
      var row = el('div', 'row-item');
      row.appendChild(el('span', null, entry.name || '(未命名)'));
      row.appendChild(el('span', 'hint', entry.username || '（无用户名）'));
      if (entry.tags && entry.tags.length) {
        row.appendChild(el('span', 'hint', '#' + entry.tags.join(' #')));
      }
      list.appendChild(row);
    });
    if (entries.length > 40) {
      list.appendChild(el('div', 'row-item', '……其余 ' + (entries.length - 40) + ' 条未列出'));
    }

    var conflictHint = $('sync-conflict-hint');
    if (data.conflict) {
      conflictHint.textContent = '冲突：两端都有各自的修改。点「应用到本机」会二次确认，并用远端数据整体覆盖本机条目。';
      conflictHint.classList.remove('hidden');
    } else {
      conflictHint.classList.add('hidden');
    }
  }

  function decisionText(decision) {
    var map = { push: '可上传（远端更旧）', pull: '可拉取（远更新）', conflict: '冲突（需确认）', none: '无变化' };
    return map[decision] || decision || '未知';
  }

  /* ========================= 10. 设置 ========================= */

  function openSettingsDialog() {
    showError('settings-error', '');
    $('settings-ok').textContent = '';
    var s = state.settings || {};
    $('s-autolock').value = s.auto_lock_minutes !== undefined ? s.auto_lock_minutes : 5;
    $('s-clipboard').value = s.clipboard_clear_seconds !== undefined ? s.clipboard_clear_seconds : 20;
    $('s-reveal').value = s.reveal_seconds !== undefined ? s.reveal_seconds : 20;
    $('s-olddays').value = s.old_password_days !== undefined ? s.old_password_days : 180;
    openDialog('dlg-settings');
  }

  function saveSettings() {
    showError('settings-error', '');
    $('settings-ok').textContent = '';
    var body = {
      auto_lock_minutes: Math.max(0, Number($('s-autolock').value) || 0),
      clipboard_clear_seconds: Math.max(0, Number($('s-clipboard').value) || 0),
      reveal_seconds: Math.max(0, Number($('s-reveal').value) || 0),
      old_password_days: Math.max(1, Number($('s-olddays').value) || 180)
    };
    apiSend('/api/settings', 'POST', body).then(function (data) {
      state.settings = (data && data.settings) || body;
      $('settings-ok').textContent = '已保存';
      toast('设置已保存', 'ok');
      state.lastActivity = Date.now();
      return reloadAll();
    }).catch(function (err) {
      showError('settings-error', err.message);
    });
  }

  function changeMasterPassword() {
    showError('settings-error', '');
    var oldPassword = window.prompt('请输入当前主密码：');
    if (oldPassword === null) { return; }
    var newPassword = window.prompt('请输入新主密码（至少 8 位）：');
    if (newPassword === null) { return; }
    if (!newPassword || newPassword.length < 8) {
      showError('settings-error', '新主密码至少 8 位');
      return;
    }
    if (newPassword !== window.prompt('请再输入一次新主密码：')) {
      showError('settings-error', '两次输入的新主密码不一致');
      return;
    }
    apiSend('/api/master-password', 'POST', { old_password: oldPassword, new_password: newPassword })
      .then(function (data) {
        $('settings-ok').textContent = '主密码已修改';
        if (data && data.recovery_key) {
          showRecoveryDialog(data.recovery_key, '主密码已修改，恢复密钥已重新生成。旧的恢复密钥立即失效，请抄写新的。');
        }
      })
      .catch(function (err) { showError('settings-error', err.message); });
  }

  function exportVault() {
    showError('settings-error', '');
    $('settings-ok').textContent = '';
    var path = $('s-export-path').value.trim();
    if (!path) {
      showError('settings-error', '请填写导出路径（绝对路径）');
      return;
    }
    apiSend('/api/export', 'POST', { path: path }).then(function (data) {
      $('settings-ok').textContent = '已导出到 ' + ((data && data.path) || path);
      toast('已导出加密副本', 'ok');
    }).catch(function (err) {
      showError('settings-error', err.message);
    });
  }

  /* ========================= 11. 恢复密钥强制确认 ========================= */

  function showRecoveryDialog(recoveryKey, reason) {
    $('recovery-value').textContent = recoveryKey;
    $('recovery-reason').textContent = reason || '请抄写并离线保存恢复密钥。';
    $('recovery-ack').checked = false;
    $('btn-recovery-done').disabled = true;
    openDialog('dlg-recovery');
  }

  function rotateRecoveryKey() {
    showError('sync-error', '');
    apiSend('/api/recovery/rotate', 'POST', {}).then(function (data) {
      if (data && data.recovery_key) {
        showRecoveryDialog(data.recovery_key, '已生成新的恢复密钥，旧密钥立即失效，请重新抄写。');
      }
    }).catch(function (err) {
      showError('sync-error', err.message);
    });
  }

  /* ========================= 12. 锁定 / 自动锁定 ========================= */

  function lockNow(silent) {
    return apiSend('/api/lock', 'POST', undefined).then(function () {
      goLocked(silent ? '' : '已锁定');
    }).catch(function () {
      goLocked('已锁定');
    });
  }

  function touchActivity() {
    state.lastActivity = Date.now();
  }

  /** 前端空闲计时：到点先调 /api/lock，再回锁屏页。后端也有兜底。 */
  function startIdleWatcher() {
    window.setInterval(function () {
      if (state.locked) { return; }
      var minutes = state.settings ? Number(state.settings.auto_lock_minutes) : 5;
      if (!minutes || minutes <= 0) { return; }
      var idleMs = Date.now() - state.lastActivity;
      if (idleMs >= minutes * 60 * 1000) {
        lockNow(false).then(function () {
          toast('空闲超时，已自动锁定', 'warn', 5000);
        });
      }
    }, 5000);
  }

  /* ========================= 13. 事件绑定 ========================= */

  function bindEvents() {
    // ---- 锁屏页 ----
    $('toggle-master').addEventListener('click', function () {
      var input = $('master-password');
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      this.textContent = show ? '隐藏' : '显示';
      input.focus();
    });

    $('btn-unlock').addEventListener('click', function () {
      var password = $('master-password').value;
      showError('unlock-error', '');
      if (!password) {
        showError('unlock-error', '请输入主密码');
        return;
      }
      if (!state.vaultExists) {
        if (password.length < 8) {
          showError('unlock-error', '主密码至少 8 位');
          return;
        }
        doUnlock('create', { mode: 'create', password: password, overwrite: false });
      } else {
        doUnlock('unlock', { mode: 'unlock', password: password });
      }
    });

    $('master-password').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        $('btn-unlock').click();
      }
    });

    $('btn-show-recovery').addEventListener('click', function () {
      var panel = $('recovery-panel');
      var hidden = panel.classList.contains('hidden');
      panel.classList.toggle('hidden', !hidden);
      if (hidden) { $('recovery-key').focus(); }
    });

    $('btn-recovery-unlock').addEventListener('click', function () {
      var key = $('recovery-key').value.trim();
      showError('unlock-error', '');
      if (!key) {
        showError('unlock-error', '请输入恢复密钥');
        return;
      }
      doUnlock('recovery', { mode: 'recovery', recovery_key: key });
    });

    $('recovery-key').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        $('btn-recovery-unlock').click();
      }
    });

    // ---- 顶栏 ----
    var searchTimer = null;
    $('search').addEventListener('input', function () {
      var value = this.value;
      if (searchTimer) { window.clearTimeout(searchTimer); }
      searchTimer = window.setTimeout(function () {
        state.query = value.trim();
        loadEntries().then(function () { renderList(); renderDetail(); })
          .catch(function (err) { toast(err.message, 'err'); });
      }, 220);   // 输入即过滤（带 220ms 去抖，走服务端 q 参数）
    });

    $('btn-new').addEventListener('click', function () { openEntryDialog(null); });
    $('btn-sync').addEventListener('click', openSyncDialog);
    $('btn-settings').addEventListener('click', openSettingsDialog);
    $('btn-lock').addEventListener('click', function () { lockNow(false); });

    // ---- 条目弹窗 ----
    $('entry-form').addEventListener('submit', function (event) {
      event.preventDefault();   // 表单只用回车提交，不真的走 dialog 提交
      saveEntry();
    });
    $('btn-save-entry').addEventListener('click', saveEntry);
    $('btn-open-generator').addEventListener('click', openGeneratorDialog);
    $('btn-add-field').addEventListener('click', function () { addCustomFieldRow('', '', false); });

    // ---- 生成器 ----
    $('gen-length').addEventListener('input', function () {
      $('gen-length-value').textContent = this.value;
      requestGeneratedPassword();
    });
    ['gen-lower', 'gen-upper', 'gen-digits', 'gen-symbols', 'gen-exclude'].forEach(function (id) {
      $(id).addEventListener('change', requestGeneratedPassword);
    });
    $('btn-gen-refresh').addEventListener('click', requestGeneratedPassword);
    $('dlg-generator').querySelector('form').addEventListener('submit', function (event) {
      event.preventDefault();
      useGeneratedPassword();
    });
    $('btn-gen-use').addEventListener('click', useGeneratedPassword);

    // ---- 同步 ----
    $('btn-toggle-sync-pass').addEventListener('click', function () {
      var input = $('sync-passphrase');
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      this.textContent = show ? '隐藏' : '显示';
    });
    $('btn-sync-pack').addEventListener('click', packSync);
    $('btn-sync-copy').addEventListener('click', function () {
      if (!state.syncOutputText) {
        showError('sync-error', '还没有生成结果');
        return;
      }
      copyToClipboard(state.syncOutputText, '同步字符串');
    });
    $('btn-sync-preview').addEventListener('click', function () { previewSync(false); });
    $('btn-sync-apply').addEventListener('click', function () { previewSync(true); });
    $('btn-rotate-recovery').addEventListener('click', rotateRecoveryKey);

    // ---- 设置 ----
    $('btn-save-settings').addEventListener('click', saveSettings);
    $('btn-change-master').addEventListener('click', changeMasterPassword);
    $('btn-export').addEventListener('click', exportVault);

    // ---- 恢复密钥确认 ----
    $('recovery-ack').addEventListener('change', function () {
      $('btn-recovery-done').disabled = !this.checked;
    });
    // 强制确认：未勾选时连 Esc / 关闭操作都拒绝（原生 dialog 的 cancel 事件）
    $('dlg-recovery').addEventListener('cancel', function (event) {
      if (!$('recovery-ack').checked) { event.preventDefault(); }
    });
    $('btn-copy-recovery').addEventListener('click', function () {
      copyToClipboard($('recovery-value').textContent, '恢复密钥');
    });
    $('btn-recovery-done').addEventListener('click', function () {
      closeDialog('dlg-recovery');
      // 关闭后不保留明文
      $('recovery-value').textContent = '—';
      $('recovery-ack').checked = false;
      $('btn-recovery-done').disabled = true;
    });

    // ---- 关闭按钮 ----
    Array.prototype.forEach.call(document.querySelectorAll('[data-close]'), function (btn) {
      btn.addEventListener('click', function () {
        closeDialog(btn.getAttribute('data-close'));
      });
    });

    // ---- 快捷键 ----
    document.addEventListener('keydown', function (event) {
      var key = (event.key || '').toLowerCase();
      if (key === 'escape') {
        // 原生 dialog 自己处理 Esc；这里兜底确保恢复密钥弹窗也关不掉未确认
        // 若开启的是恢复密钥弹窗且未勾选，则阻止关闭
        var rec = $('dlg-recovery');
        if (rec && rec.hasAttribute('open') && rec.open && !$('recovery-ack').checked) {
          event.preventDefault();
        }
        return;
      }
      if (!event.ctrlKey || event.altKey || event.metaKey) { return; }
      if (key === 'f') {
        event.preventDefault();
        $('search').focus();
        $('search').select();
      } else if (key === 'n') {
        event.preventDefault();
        openEntryDialog(null);
      } else if (key === 'l') {
        event.preventDefault();
        if (!state.locked) { lockNow(false); }
      }
    });

    // ---- 空闲活动监听 ----
    ['mousedown', 'mousemove', 'keydown', 'wheel', 'scroll', 'touchstart'].forEach(function (evt) {
      window.addEventListener(evt, touchActivity, true);
    });
  }

  /* ========================= 14. 启动 ========================= */

  PM.init = function init() {
    if (PM._started) { return; }
    PM._started = true;
    bindEvents();
    startIdleWatcher();
    refreshLockInfo();
    $('master-password').focus();
  };

  window.PM = PM;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', PM.init);
  } else {
    PM.init();
  }
})();
