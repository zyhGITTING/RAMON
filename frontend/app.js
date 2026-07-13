let currentPlat = null, expandedCard = null, currentAdminView = null;

function renderPlatform() {
  // 未登录时显示欢迎页
  document.getElementById('mainContent').innerHTML = `
  <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:60vh;text-align:center;color:#94a3b8;">
    <img src="leimo.png" alt="镭目科技" style="width:200px;height:auto;margin-bottom:24px;object-fit:contain;">
    <h2 style="font-size:18px;font-weight:600;color:#64748b;margin-bottom:8px;">镭目科技 · 数据中台</h2>
    <p style="font-size:13px;">请先登录以查看平台接口数据</p>
  </div>`;
}

function updateGlobalStatus() {
  document.getElementById('updateTime').textContent = new Date().toLocaleString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
renderPlatform(); updateGlobalStatus();

(function() {
  function normalizeBasePath(path) {
    let value = String(path || '').trim();
    if (!value || value === '/') return '';
    if (!value.startsWith('/')) value = '/' + value;
    value = value.replace(/\/+/g, '/').replace(/\/$/, '');
    return value === '/' ? '' : value;
  }
  function detectBasePath() {
    const PLACEHOLDER = '__DATAMID_BASE_PATH__';
    if (typeof window.__DATAMID_BASE_PATH__ !== 'undefined' && window.__DATAMID_BASE_PATH__ !== null && window.__DATAMID_BASE_PATH__ !== PLACEHOLDER) {
      return normalizeBasePath(window.__DATAMID_BASE_PATH__);
    }
    try {
      const params = new URLSearchParams(window.location.search);
      const v = params.get('__base_path') || params.get('base_path');
      if (v) return normalizeBasePath(v);
    } catch (e) {}
    const existing = document.querySelector('base');
    if (existing && existing.href) {
      try { return normalizeBasePath(new URL(existing.href).pathname); } catch (e) {}
    }
    if (window.location.protocol === 'file:') return '';
    const parts = window.location.pathname.split('/').filter(Boolean);
    const last = parts[parts.length - 1] || '';
    if (last === 'index.html' || last === 'dashboard.html') parts.pop();
    return normalizeBasePath(parts.length ? '/' + parts.join('/') : '');
  }
  const BASE_PATH = detectBasePath();
  const API_BASE = window.location.protocol === 'file:'
    ? 'http://127.0.0.1:8128'
    : window.location.origin + BASE_PATH;
  const ERP_SOURCE_MAP = {'erp-buy':'erp_buy',stock:'stock',srm:'srm_purchase',};

  const AUDIT_ACTION_LABELS = {
    view_data: '查看数据',
    mcp_query: 'MCP 数据查询',
    mcp_tool_call: 'MCP 工具调用',
    mcp_export: '生成 MCP 令牌',
    mcp_export_http: '生成 MCP HTTP 令牌',
    create_user: '创建用户',
    delete_user: '删除用户',
    set_department: '设置部门',
    reset_password: '重置密码',
    set_user_permissions: '设置用户数据源权限',
    set_department_permissions: '设置部门数据源权限',
    set_user_field_permissions: '设置用户字段权限',
    set_department_field_permissions: '设置部门字段权限',
    set_field_restriction: '设置字段限制',
    create_platform: '创建平台',
    update_platform: '更新平台',
    delete_platform: '删除平台',
    create_llm_service: '创建 LLM 服务',
    update_llm_service: '更新 LLM 服务',
    delete_llm_service: '删除 LLM 服务',
    parse_doc: '解析数据源文档',
    parse_datasource_doc: '解析数据源文档',
    create_datasource: '创建数据源',
    update_datasource: '更新数据源',
    delete_datasource: '删除数据源',
    rollback_datasource_snapshot: '回滚数据源快照',
    trigger_sync: '手动触发同步',
    create_mcp_export_request: '申请 MCP 导出',
    handle_mcp_export_request: '审批 MCP 导出申请',
    revoke_mcp_token: '吊销 MCP 令牌',
    revoke_mcp_token_self: '用户吊销 MCP 令牌',
    delete_mcp_token_self: '用户删除 MCP 令牌',
    view_api_doc: '查看接口文档',
    change_password: '修改密码',
    yanhuang_auto_register: '炎黄自动注册',
    url_sso_auto_register: 'URL SSO 自动注册',
    url_sso_login: 'URL SSO 登录',
  };
  const ROLE_LABELS = { admin: '管理员', user: '用户', system: '系统' };

  const state = {
    token: localStorage.getItem('datamid_token') || '',
    user: null,
    datasources: [],
    dynamicPlatforms: [],
    dataCache: {},
    catalogItems: [],
    catalogKeyword: '',
    admin: {stats:null,users:[],syncLogs:[],auditLogs:[],auditKeyword:'',mcpKeyword:'',mcpStatus:'active',mcpReqKeyword:'',mcpReqStatus:'pending'},
  };
  const features = {
    selfRegisterEnabled: false,
    devSsoEnabled: false,
    yanhuangSsoEnabled: false,
  };
  window._dmDatasourceDraftFieldLabels = {};
  window._dmDatasourceLastParse = null;

  function $(id) { return document.getElementById(id); }
  function escapeHtml(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  function prettyJson(value) { return JSON.stringify(value || {}, null, 2); }
  function parseCommaList(raw) {
    return String(raw || '').split(/[;,\n]/).map(s => s.trim()).filter(Boolean);
  }
  function parseJsonInput(raw, label) {
    const text = (raw || '').trim();
    if (!text) return {};
    try { return JSON.parse(text); }
    catch { throw new Error(label + ' 不是合法 JSON'); }
  }
  function renderParameterDocs(requestConfig) {
    const docs = Array.isArray(requestConfig && requestConfig.parameter_docs) ? requestConfig.parameter_docs : [];
    if (!docs.length) return '';
    const rows = docs.map(item => {
      const name = escapeHtml(item.name || '');
      const label = escapeHtml(item.label || item.name || '');
      const desc = escapeHtml(item.description || '');
      return `
        <div style="display:grid;grid-template-columns:140px 120px minmax(0,1fr);gap:12px;padding:10px 0;border-top:1px solid #e2e8f0;">
          <div style="font-family:monospace;font-size:12px;color:#334155;word-break:break-all;">${name}</div>
          <div style="font-size:12px;color:#0f172a;font-weight:600;">${label}</div>
          <div style="font-size:12px;color:#64748b;line-height:1.6;">${desc}</div>
        </div>`;
    }).join('');
    return `
      <div style="margin-bottom:18px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;padding:14px 16px;">
        <div style="font-size:14px;font-weight:600;color:#1e293b;margin-bottom:6px;">请求参数说明</div>
        <div style="display:grid;grid-template-columns:140px 120px minmax(0,1fr);gap:12px;padding:0 0 8px 0;font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;">
          <div>参数名</div>
          <div>中文名</div>
          <div>说明</div>
        </div>
        ${rows}
      </div>`;
  }
  function syncAuthFeatureUi() {
    const allowRegister = features.selfRegisterEnabled;
    const showRegisterBtn = $('dmShowRegister');
    const registerTabBtn = $('dmAuthTabReg');
    const loginSwitchBtn = $('dmShowLogin');
    const loginTabBtn = $('dmAuthTabLogin');
    const registerForm = $('dmRegisterForm');
    const loginForm = $('dmLoginForm');
    const registerPanel = $('dmAuthRegPanel');
    const loginPanel = $('dmAuthLoginPanel');
    if (showRegisterBtn) {
      showRegisterBtn.style.display = allowRegister ? '' : 'none';
      showRegisterBtn.classList.remove('active');
    }
    if (registerTabBtn) {
      registerTabBtn.style.display = allowRegister ? '' : 'none';
    }
    if (!allowRegister) {
      if (registerForm && loginForm) {
        registerForm.classList.add('dm-hidden');
        loginForm.classList.remove('dm-hidden');
      }
      if (registerPanel && loginPanel) {
        registerPanel.style.display = 'none';
        loginPanel.style.display = '';
      }
      if (loginSwitchBtn) loginSwitchBtn.classList.add('active');
      if (loginTabBtn) {
        loginTabBtn.style.background = '#fff';
        loginTabBtn.style.color = '#111827';
        loginTabBtn.style.fontWeight = '600';
        loginTabBtn.style.boxShadow = '0 1px 3px rgba(0,0,0,0.1)';
      }
    }
  }
  async function loadPublicConfig() {
    try {
      const resp = await fetch(API_BASE + '/api/public/config');
      if (!resp.ok) return;
      const data = await resp.json();
      features.selfRegisterEnabled = !!data.self_register_enabled;
      features.devSsoEnabled = !!data.dev_sso_enabled;
      features.yanhuangSsoEnabled = !!data.yanhuang_sso_enabled;
    } catch(e) {}
    syncAuthFeatureUi();
  }
  function fmtHMS(totalSeconds) {
    if (totalSeconds == null || totalSeconds < 0) return '—';
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    const parts = [];
    if (h) parts.push(`${h} 小时`);
    if (m) parts.push(`${m} 分`);
    if (s || !parts.length) parts.push(`${s} 秒`);
    return parts.join(' ');
  }
  function parseHMS(h, m, s) {
    return (parseInt(h) || 0) * 3600 + (parseInt(m) || 0) * 60 + (parseInt(s) || 0);
  }
  function hmsInputsHtml(namePrefix, secondsValue, opts = {}) {
    const v = Math.max(0, parseInt(secondsValue) || 0);
    const h = Math.floor(v / 3600);
    const m = Math.floor((v % 3600) / 60);
    const s = v % 60;
    const cls = opts.small ? 'style="width:46px;font-size:11px;padding:2px 4px;"' : 'style="width:56px;font-size:12px;padding:3px 6px;"';
    return `
      <div style="display:flex;align-items:center;gap:4px;">
        <input type="number" min="0" max="168" value="${h}" id="${namePrefix}H" ${cls} class="dm-hms-input" placeholder="时">
        <span style="font-size:11px;color:#64748b;">时</span>
        <input type="number" min="0" max="59" value="${m}" id="${namePrefix}M" ${cls} class="dm-hms-input" placeholder="分">
        <span style="font-size:11px;color:#64748b;">分</span>
        <input type="number" min="0" max="59" value="${s}" id="${namePrefix}S" ${cls} class="dm-hms-input" placeholder="秒">
        <span style="font-size:11px;color:#64748b;">秒</span>
      </div>`;
  }
  function formatStatusText(status) {
    const map = {
      success: '成功',
      empty: '无数据',
      warning: '告警',
      failed: '失败',
      syncing: '同步中',
      idle: '闲置',
      active: '启用',
      disabled: '停用',
      configured: '已配置',
      na: '暂无',
    };
    return map[status] || status || '—';
  }

  async function apiFetch(path, opts) {
    const headers = opts&&opts.headers||{};
    if (state.token) {
      if (headers['X-Skip-Auth']) { delete headers['X-Skip-Auth']; }
      else { headers['Authorization'] = 'Bearer ' + state.token; }
    }
    // POST 需要 Content-Type
    if (opts && opts.method) {
      headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    }
    const res = await fetch(API_BASE + path, { ...opts, headers });
    if (!res.ok) {
      const body = await res.json().catch(()=>({}));
      const detail = body.detail;
      const msg = Array.isArray(detail) ? detail.map(e=>e.msg||JSON.stringify(e)).join('; ') : (detail || `HTTP ${res.status}`);
      throw new Error(msg);
    }
    return res.json();
  }

  function showToast(msg, isError) {
    const el = $('dmToast');
    el.textContent = msg; el.className = 'dm-toast show' + (isError?' error':'');
    clearTimeout(el._t); el._t = setTimeout(()=>{el.className='dm-toast';}, 2500);
  }

  // ======== 登录状态更新 ========
  function updateLoginUI() {
    const loggedIn = !!(state.user);
    $('dmNavLoginBtn').classList.toggle('dm-hidden', loggedIn);
    $('dmNavChangePwdBtn').classList.toggle('dm-hidden', !loggedIn);
    $('dmNavLogoutBtn').classList.toggle('dm-hidden', !loggedIn);
    $('dmNavChip').textContent = loggedIn ? (state.user.full_name || state.user.username) : '未登录';
    const isAdmin = loggedIn && state.user.role === 'admin';
    $('adminSidebarSection').classList.toggle('dm-hidden', !isAdmin);
    $('userSidebarSection').classList.toggle('dm-hidden', !loggedIn);
    if (!isAdmin) { currentAdminView = null; }
    if (isAdmin) startMcpRequestPolling(); else stopMcpRequestPolling();
    if (loggedIn) startUserMsgPolling(); else stopUserMsgPolling();
    // 全屏认证页
    const authScreen = $('dmAuthScreen');
    if (authScreen) authScreen.style.display = loggedIn ? 'none' : 'flex';
  }

  // ======== 全屏认证页逻辑（全局挂载，供 onclick 调用）========
  window.dmSwitchAuthTab = function(tab) {
    if (tab !== 'login' && !features.selfRegisterEnabled) {
      showToast('自助注册已关闭，请联系管理员开通账号', true);
      return;
    }
    const isLogin = tab === 'login';
    $('dmAuthLoginPanel').style.display = isLogin ? '' : 'none';
    $('dmAuthRegPanel').style.display = isLogin ? 'none' : '';
    const loginBtn = $('dmAuthTabLogin'), regBtn = $('dmAuthTabReg');
    loginBtn.style.background = isLogin ? '#fff' : 'transparent';
    loginBtn.style.color = isLogin ? '#111827' : '#6b7280';
    loginBtn.style.fontWeight = isLogin ? '600' : '500';
    loginBtn.style.boxShadow = isLogin ? '0 1px 3px rgba(0,0,0,0.1)' : 'none';
    regBtn.style.background = isLogin ? 'transparent' : '#fff';
    regBtn.style.color = isLogin ? '#6b7280' : '#111827';
    regBtn.style.fontWeight = isLogin ? '500' : '600';
    regBtn.style.boxShadow = isLogin ? 'none' : '0 1px 3px rgba(0,0,0,0.1)';
  };

  function _onAuthSuccess(data) {
    state.token = data.token; state.user = data.user;
    localStorage.setItem('datamid_token', state.token);
    $('dmAuthScreen').style.display = 'none';
    updateLoginUI(); renderPlatform(currentPlat);
    loadDynamicPlatforms();
    if (state.user.role === 'admin') loadAdminData().catch(()=>{});
    // 已取消登录后强制改密（用户自助注册时自行设置密码）
    // _checkForcePasswordChange();
  }

  // ======== URL 参数单点登录（从 ?username=xxx 跳转自动登录） ========
  async function _decodeUrlUsername(encoded) {
    if (!encoded) return '';
    try {
      // Base64 -> Uint8Array
      const binary = atob(encoded.replace(/-/g, '+').replace(/_/g, '/'));
      const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
      // gzip 解压
      let decompressed;
      if (typeof window.pako !== 'undefined' && window.pako.ungzip) {
        decompressed = window.pako.ungzip(bytes);
      } else if (typeof DecompressionStream !== 'undefined') {
        const ds = new DecompressionStream('gzip');
        const writer = ds.writable.getWriter();
        writer.write(bytes); writer.close();
        const reader = ds.readable.getReader();
        const chunks = [];
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
        }
        const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
        decompressed = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of chunks) {
          decompressed.set(chunk, offset);
          offset += chunk.length;
        }
      } else {
        throw new Error('没有可用的 gzip 解压库');
      }
      return new TextDecoder().decode(decompressed).trim();
    } catch (e) {
      // 不是 gzip+base64 时按原样返回，兼容外部系统直接传明文
      return encoded.trim();
    }
  }

  async function _tryUrlSsoLogin() {
    if (state.token) return;
    let encoded = '';
    try {
      encoded = new URLSearchParams(window.location.search).get('username') || '';
    } catch (e) {}
    if (!encoded) return;
    const username = await _decodeUrlUsername(encoded);
    if (!username) {
      showToast('用户名参数解码失败', true);
      return;
    }
    try {
      const resp = await fetch(API_BASE + '/api/auth/sso/url?username=' + encodeURIComponent(username), {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `SSO 登录失败: HTTP ${resp.status}`);
      }
      const data = await resp.json();
      _onAuthSuccess(data);
      showToast(`欢迎，${data.user.full_name || data.user.username}`);
      // 登录成功后去掉 URL 中的 username 参数，避免刷新重复登录
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete('username');
        window.history.replaceState({}, '', url.toString());
      } catch (e) {}
    } catch (e) {
      showToast(e.message, true);
    }
  }

  // ======== 强制修改初始密码 ========
  function _checkForcePasswordChange() {
    const mustChange = !!(state.user && state.user.must_change_password);
    $('dmForcePwdMask').classList.toggle('dm-hidden', !mustChange);
    if (mustChange) {
      $('dmForcePwdNew').value = ''; $('dmForcePwdNew2').value = '';
      $('dmForcePwdErr').style.display = 'none';
    }
  }

  function _validatePasswordClientSide(pwd) {
    if (pwd.length < 8) return '密码长度至少 8 位';
    if (!/[a-z]/.test(pwd)) return '密码必须包含小写字母';
    if (!/[A-Z]/.test(pwd)) return '密码必须包含大写字母';
    if (!/\d/.test(pwd)) return '密码必须包含数字';
    if (!/[^a-zA-Z0-9]/.test(pwd)) return '密码必须包含符号（如 !@#$%）';
    if (/(.)\1\1/.test(pwd)) return '密码不能包含 3 个及以上连续重复字符';
    return '';
  }
  window._validatePasswordClientSide = _validatePasswordClientSide;

  function _validateEmployeeNo(value) {
    if (!/^120[0-9]{7}$/.test(value)) return '工号必须是 120 开头的 10 位数字';
    return '';
  }
  window._validateEmployeeNo = _validateEmployeeNo;

  $('dmForcePwdSubmit').addEventListener('click', async () => {
    const newPwd = $('dmForcePwdNew').value || '';
    const newPwd2 = $('dmForcePwdNew2').value || '';
    const errEl = $('dmForcePwdErr');
    errEl.style.display = 'none';
    if (!newPwd || !newPwd2) { errEl.textContent = '请填写完整'; errEl.style.display = ''; return; }
    if (newPwd !== newPwd2) { errEl.textContent = '两次输入的新密码不一致'; errEl.style.display = ''; return; }
    const clientErr = _validatePasswordClientSide(newPwd);
    if (clientErr) { errEl.textContent = clientErr; errEl.style.display = ''; return; }
    try {
      const resp = await apiFetch('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ new_password: newPwd }),
      });
      state.user = resp.user || state.user;
      $('dmForcePwdMask').classList.add('dm-hidden');
      showToast('密码修改成功');
    } catch (e) {
      errEl.textContent = e.message;
      errEl.style.display = '';
    }
  });
  ['dmForcePwdNew', 'dmForcePwdNew2'].forEach(id => {
    $(id).addEventListener('keydown', e => { if (e.key === 'Enter') $('dmForcePwdSubmit').click(); });
  });

  function openChangePasswordDialog() {
    if (!state.user) { showToast('请先登录', true); return; }
    $('dmChangePwdOld').value = '';
    $('dmChangePwdNew').value = '';
    $('dmChangePwdNew2').value = '';
    $('dmChangePwdErr').style.display = 'none';
    $('dmChangePwdMask').classList.remove('dm-hidden');
    setTimeout(() => $('dmChangePwdOld').focus(), 0);
  }

  function closeChangePasswordDialog() {
    $('dmChangePwdMask').classList.add('dm-hidden');
  }

  async function submitChangePassword() {
    const oldPwd = $('dmChangePwdOld').value || '';
    const newPwd = $('dmChangePwdNew').value || '';
    const newPwd2 = $('dmChangePwdNew2').value || '';
    const errEl = $('dmChangePwdErr');
    errEl.style.display = 'none';
    if (!oldPwd || !newPwd || !newPwd2) { errEl.textContent = '请填写完整'; errEl.style.display = ''; return; }
    if (newPwd !== newPwd2) { errEl.textContent = '两次输入的新密码不一致'; errEl.style.display = ''; return; }
    const clientErr = _validatePasswordClientSide(newPwd);
    if (clientErr) { errEl.textContent = clientErr; errEl.style.display = ''; return; }
    const btn = $('dmChangePwdSubmit');
    const oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '修改中...';
    try {
      const resp = await apiFetch('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
      });
      state.user = resp.user || state.user;
      closeChangePasswordDialog();
      showToast('密码修改成功');
    } catch (e) {
      errEl.textContent = e.message;
      errEl.style.display = '';
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }

  $('dmNavChangePwdBtn').addEventListener('click', openChangePasswordDialog);
  $('dmChangePwdClose').addEventListener('click', closeChangePasswordDialog);
  $('dmChangePwdCancel').addEventListener('click', closeChangePasswordDialog);
  $('dmChangePwdSubmit').addEventListener('click', submitChangePassword);
  $('dmChangePwdMask').addEventListener('click', e => { if (e.target === $('dmChangePwdMask')) closeChangePasswordDialog(); });
  ['dmChangePwdOld', 'dmChangePwdNew', 'dmChangePwdNew2'].forEach(id => {
    $(id).addEventListener('keydown', e => { if (e.key === 'Enter') submitChangePassword(); });
  });

  window.dmAuthDoLogin = async function() {
    const user = ($('dmAuthUser').value || '').trim();
    const pwd = $('dmAuthPwd').value || '';
    const errEl = $('dmAuthLoginErr');
    errEl.style.display = 'none';
    if (!user || !pwd) { errEl.textContent = '请输入用户名和密码'; errEl.style.display = ''; return; }
    try {
      const resp = await fetch(API_BASE + '/api/auth/login', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({username: user, password: pwd}),
      });
      if (!resp.ok) { const b = await resp.json().catch(()=>({})); throw new Error(b.detail||'登录失败'); }
      _onAuthSuccess(await resp.json());
      showToast('登录成功');
    } catch(err) { errEl.textContent = err.message; errEl.style.display = ''; }
  };

  window.dmAuthDoRegister = async function() {
    if (!features.selfRegisterEnabled) {
      const errEl = $('dmAuthRegErr');
      errEl.textContent = '自助注册已关闭，请联系管理员开通账号';
      errEl.style.display = '';
      return;
    }
    const emp = ($('dmAuthRegEmp').value||'').trim();
    const uname = ($('dmAuthRegUname').value||'').trim();
    const name = ($('dmAuthRegName').value||'').trim();
    const pwd = $('dmAuthRegPwd').value||'';
    const pwd2 = $('dmAuthRegPwd2').value||'';
    const errEl = $('dmAuthRegErr');
    errEl.style.display = 'none';
    const empErr = _validateEmployeeNo(emp);
    if (empErr) { errEl.textContent = empErr; errEl.style.display = ''; return; }
    if (uname !== emp) { errEl.textContent = '用户名必须与工号一致'; errEl.style.display = ''; return; }
    if (pwd !== pwd2) { errEl.textContent = '两次密码不一致'; errEl.style.display = ''; return; }
    const pwdErr = _validatePasswordClientSide(pwd);
    if (pwdErr) { errEl.textContent = pwdErr; errEl.style.display = ''; return; }
    try {
      const resp = await fetch(API_BASE + '/api/auth/register', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({employee_no:emp, username:uname, full_name:name, password:pwd}),
      });
      if (!resp.ok) { const b = await resp.json().catch(()=>({})); throw new Error(b.detail||'注册失败'); }
      _onAuthSuccess(await resp.json());
      showToast('注册成功');
    } catch(err) { errEl.textContent = err.message; errEl.style.display = ''; }
  };

  // Enter 键触发认证
  ['dmAuthUser','dmAuthPwd'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') window.dmAuthDoLogin(); });
  });
  ['dmAuthRegEmp','dmAuthRegUname','dmAuthRegName','dmAuthRegPwd','dmAuthRegPwd2'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') window.dmAuthDoRegister(); });
  });

  // ======== 登录/注册浮层 ========
  $('dmNavLoginBtn').addEventListener('click', ()=>{ $('dmLoginMask').classList.remove('dm-hidden'); });
  $('dmNavLogoutBtn').addEventListener('click', ()=>{
    state.token = ''; state.user = null; localStorage.removeItem('datamid_token');
    state.dynamicPlatforms = []; state.catalogItems = []; rebuildDynamicNav();
    updateLoginUI(); renderPlatform(currentPlat); currentAdminView = null;
    $('dmAuthScreen').style.display = 'flex';
    showToast('已退出登录');
  });

  // Login/Register tab switch
  $('dmShowLogin').addEventListener('click',()=>{ $('dmLoginForm').classList.remove('dm-hidden');$('dmRegisterForm').classList.add('dm-hidden');$('dmShowLogin').classList.add('active');$('dmShowRegister').classList.remove('active'); });
  $('dmShowRegister').addEventListener('click',()=>{
    if (!features.selfRegisterEnabled) {
      showToast('自助注册已关闭，请联系管理员开通账号', true);
      return;
    }
    $('dmRegisterForm').classList.remove('dm-hidden');$('dmLoginForm').classList.add('dm-hidden');$('dmShowRegister').classList.add('active');$('dmShowLogin').classList.remove('active');
  });

  $('dmLoginForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    try {
      const resp = await fetch(API_BASE + '/api/auth/login', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:$('dmUsername').value.trim(),password:$('dmPassword').value}),
      });
      if (!resp.ok) { const b = await resp.json().catch(()=>({})); throw new Error(b.detail||'登录失败'); }
      const data = await resp.json();
      state.token = data.token; state.user = data.user;
      localStorage.setItem('datamid_token', state.token);
      $('dmLoginMask').classList.add('dm-hidden');
      updateLoginUI(); renderPlatform(currentPlat);
      loadDynamicPlatforms();
      showToast('登录成功');
    } catch(err) { showToast(err.message, true); }
  });

  $('dmRegisterForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    if (!features.selfRegisterEnabled) return showToast('自助注册已关闭，请联系管理员开通账号', true);
    const emp = ($('dmRegisterEmployeeNo').value||'').trim();
    const uname = ($('dmRegisterUsername').value||'').trim();
    const fullName = ($('dmRegisterFullName').value||'').trim();
    const pw = $('dmRegisterPassword').value, cpw = $('dmRegisterConfirmPassword').value;
    const empErr = _validateEmployeeNo(emp);
    if (empErr) return showToast(empErr, true);
    if (uname !== emp) return showToast('用户名必须与工号一致', true);
    if (pw !== cpw) return showToast('两次密码不一致', true);
    const pwErr = _validatePasswordClientSide(pw);
    if (pwErr) return showToast(pwErr, true);
    try {
      const resp = await fetch(API_BASE + '/api/auth/register', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          employee_no:emp, username:uname, full_name:fullName, password:pw,
        }),
      });
      if (!resp.ok) { const b = await resp.json().catch(()=>({})); throw new Error(b.detail||'注册失败'); }
      const data = await resp.json();
      state.token = data.token; state.user = data.user;
      localStorage.setItem('datamid_token', state.token);
      $('dmLoginMask').classList.add('dm-hidden');
      updateLoginUI(); renderPlatform(currentPlat);
      loadDynamicPlatforms();
      showToast('注册成功');
    } catch(err) { showToast(err.message, true); }
  });

  async function loadUser() {
    if (!state.token) return;
    try { state.user = (await apiFetch('/api/auth/me', {method:'POST'})).user; }
    catch(e) { state.token = ''; state.user = null; localStorage.removeItem('datamid_token'); }
  }

  // ======== 动态平台导航 ========
  const NAV_ICON_PATHS = {
    database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    key: '<path d="M21 2l-2 2"/><path d="M11.5 7.5a5.5 5.5 0 1 0 5 5L22 18v3h-3v-3h-3l-1.5-1.5"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor" stroke="none"/>'
  };
  function navIconSvg(name, size) {
    const s = size || 17;
    return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${NAV_ICON_PATHS[name] || NAV_ICON_PATHS.database}</svg>`;
  }
  // 平台图标 = 名称首字符（中文取第一个汉字，英文/数字取第一个字符并大写），
  // 参照炎黄平台的做法：ERP 显示大写 E，避免用不相关的图形图标
  function platformMonogram(name) {
    const trimmed = (name || '').trim();
    if (!trimmed) return '?';
    return trimmed[0].toUpperCase();
  }
  function platformIconHtml(name) {
    return `<span style="font-size:13px;font-weight:700;letter-spacing:0;">${escapeHtml(platformMonogram(name))}</span>`;
  }
  const PLAT_COLORS = ['blue','purple','amber','emerald','rose','cyan','orange','teal','indigo','pink'];

  async function loadDynamicPlatforms() {
    if (!state.token) return;
    try {
      const r = await apiFetch('/api/platforms');
      state.dynamicPlatforms = r.items || [];
    } catch(e) { state.dynamicPlatforms = []; }
    rebuildDynamicNav();
  }

  function rebuildDynamicNav() {
    document.querySelectorAll('#platNav [data-plat-dynamic], #platNav [data-plat-static]').forEach(el => el.remove());
    const nav = $('platNav');
    let firstEl = null, firstP = null, firstColor = null;

    if (state.user) {
      const catEl = document.createElement('div');
      catEl.className = 'plat-item';
      catEl.dataset.platStatic = 'catalog';
      catEl.innerHTML = `<div class="plat-icon bg-slate-600/20 text-slate-300">${navIconSvg('database')}</div><span>资产目录</span>`;
      catEl.addEventListener('click', () => {
        document.querySelectorAll('#platNav .plat-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#adminSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#userSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        catEl.classList.add('active');
        currentPlat = 'catalog'; expandedCard = null; currentAdminView = null;
        renderCatalogView().catch(e => showToast(e.message, true));
      });
      nav.appendChild(catEl);

      const myMcpEl = document.createElement('div');
      myMcpEl.className = 'plat-item';
      myMcpEl.dataset.platStatic = 'my_mcp';
      myMcpEl.innerHTML = `<div class="plat-icon bg-teal-600/20 text-teal-400">${navIconSvg('key')}</div><span>我的 MCP 列表</span>`;
      myMcpEl.addEventListener('click', () => {
        document.querySelectorAll('#platNav .plat-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#adminSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#userSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        myMcpEl.classList.add('active');
        currentPlat = 'my_mcp'; expandedCard = null; currentAdminView = null;
        renderMyMcpTokens().catch(e => showToast(e.message, true));
      });
      nav.appendChild(myMcpEl);

      const divider = document.createElement('div');
      divider.className = 'border-t border-slate-700/30 my-2 mx-3';
      nav.appendChild(divider);

      const userNav = $('userSideNav');
      userNav.innerHTML = '';
      const msgEl = document.createElement('div');
      msgEl.className = 'admin-nav-item';
      msgEl.dataset.userNav = 'messages';
      msgEl.innerHTML = `<span class="nav-text"><svg class="nav-ic" viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>我的消息</span><span class="nav-badge" id="dmUserMsgBadge">0</span>`;
      msgEl.addEventListener('click', () => {
        document.querySelectorAll('#platNav .plat-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#adminSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#userSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        msgEl.classList.add('active');
        currentPlat = 'messages'; expandedCard = null; currentAdminView = null;
        renderMyMessages().catch(e => showToast(e.message, true));
      });
      userNav.appendChild(msgEl);
      refreshUserMsgBadge();
    }

    state.dynamicPlatforms.forEach((p, i) => {
      const color = PLAT_COLORS[i % PLAT_COLORS.length];
      const el = document.createElement('div');
      el.className = 'plat-item';
      el.dataset.platDynamic = p.id;
      el.innerHTML = `<div class="plat-icon bg-${color}-600/20 text-${color}-400">${platformIconHtml(p.name)}</div><span>${escapeHtml(p.name)}</span>`;
      el.addEventListener('click', () => {
        document.querySelectorAll('#platNav .plat-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#adminSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        document.querySelectorAll('#userSideNav .admin-nav-item').forEach(e => e.classList.remove('active'));
        el.classList.add('active');
        currentPlat = p.id; expandedCard = null; currentAdminView = null;
        renderDynamicPlatform(p, color);
      });
      nav.appendChild(el);
      if (!firstEl) { firstEl = el; firstP = p; firstColor = color; }
    });

    if (currentPlat === 'catalog' && !currentAdminView) {
      const activeCatalog = nav.querySelector('[data-plat-static="catalog"]');
      if (activeCatalog) activeCatalog.classList.add('active');
      renderCatalogView().catch(e => showToast(e.message, true));
      return;
    }

    if (currentPlat === 'messages' && !currentAdminView) {
      const activeMsg = document.querySelector('#userSideNav [data-user-nav="messages"]');
      if (activeMsg) activeMsg.classList.add('active');
      renderMyMessages().catch(e => showToast(e.message, true));
      return;
    }

    if (currentPlat === 'my_mcp' && !currentAdminView) {
      const activeMcp = nav.querySelector('[data-plat-static="my_mcp"]');
      if (activeMcp) activeMcp.classList.add('active');
      renderMyMcpTokens().catch(e => showToast(e.message, true));
      return;
    }

    if (firstEl && !currentAdminView && !document.querySelector('#platNav .plat-item.active')) {
      firstEl.classList.add('active');
      currentPlat = firstP.id;
      renderDynamicPlatform(firstP, firstColor);
    }
    if (currentPlat && !currentAdminView && currentPlat !== 'catalog') {
      const activeP = state.dynamicPlatforms.find(p => p.id === currentPlat);
      if (activeP) {
        const i = state.dynamicPlatforms.indexOf(activeP);
        const activeEl = nav.querySelector(`[data-plat-dynamic="${currentPlat}"]`);
        if (activeEl) activeEl.classList.add('active');
        renderDynamicPlatform(activeP, PLAT_COLORS[i % PLAT_COLORS.length]);
      }
    }
    const total = state.dynamicPlatforms.reduce((s, p) => s + (p.datasources||[]).length, 0);
    const synced = state.dynamicPlatforms.reduce((s, p) => s + (p.datasources||[]).filter(d => ['success','empty'].includes(d.last_status)).length, 0);
    document.getElementById('onlineCount').textContent = `${synced}/${total} 已同步`;
  }

  async function renderCatalogView() {
    const keyword = state.catalogKeyword || '';
    const resp = await apiFetch('/api/catalog?keyword=' + encodeURIComponent(keyword));
    state.catalogItems = resp.items || [];
    const items = state.catalogItems;
    const html = `
    <div class="mb-5">
      <h1 class="text-xl font-bold text-slate-800">资产目录</h1>
      <p class="text-sm text-slate-500 mt-1">按业务描述优先浏览数据资产，再查看技术信息与预览权限。</p>
    </div>
    <div class="admin-panel" style="margin-bottom:16px;">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <input class="dm-search" id="dmCatalogKeyword" placeholder="按资产名称、平台或描述搜索" value="${escapeHtml(keyword)}" style="flex:1;min-width:220px;max-width:420px;">
        <button class="dm-btn primary" id="dmCatalogSearch">搜索</button>
      </div>
    </div>
    ${items.length ? `<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">${items.map(item => {
      const permText = item.has_permission ? '已授权' : '仅预览';
      const permColor = item.has_permission ? '#16a34a' : '#ca8a04';
      const qualityText = formatStatusText(item.last_quality_status || 'na');
      return `<div class="api-card">
        <div class="p-4" style="display:flex;flex-direction:column;gap:10px;">
          <div style="display:flex;align-items:flex-start;gap:10px;">
            <div style="flex:1;min-width:0;">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <h3 class="text-sm font-semibold text-slate-800">${escapeHtml(item.source_name)}</h3>
                <span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#f8fafc;color:${permColor};border:1px solid #e2e8f0;">${permText}</span>
                <span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#f8fafc;color:#475569;border:1px solid #e2e8f0;">质量: ${escapeHtml(qualityText)}</span>
              </div>
              <p class="text-xs text-slate-500 mt-2">${escapeHtml(item.description || item.source_name)}</p>
            </div>
            <button class="dm-btn primary" style="padding:6px 12px;font-size:12px;white-space:nowrap;" onclick="openDsDetail('${escapeHtml(item.source_key)}')">查看</button>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#64748b;">
            <span style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;">${escapeHtml(item.platform_name || '未分配平台')}</span>
            <span style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;">字段数: ${item.field_count || 0}</span>
            ${item.row_count != null ? `<span style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;">数据量: ${(item.row_count||0).toLocaleString()}</span>` : ''}
            <span style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;">最近同步: ${escapeHtml(item.last_sync_at || '从未同步')}</span>
          </div>
          <div style="border-top:1px dashed #e2e8f0;padding-top:10px;display:flex;flex-direction:column;gap:6px;">
            <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;">业务视角</div>
            <div style="font-size:12px;color:#475569;">未授权时仍可预览字段与演示数据，真实记录会在授权后展示。</div>
            <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;">技术视角</div>
            <div style="font-size:12px;color:#475569;display:flex;gap:10px;flex-wrap:wrap;">
              <span><code>${escapeHtml(item.source_key)}</code></span>
              <span><code>${escapeHtml((item.table_name || (item.technical && item.technical.table_name)) || '')}</code></span>
              <span>${escapeHtml((item.http_method || (item.technical && item.technical.http_method)) || '')}</span>
            </div>
          </div>
        </div>
      </div>`;
    }).join('')}</div>` : `<div class="admin-panel"><div style="color:#94a3b8;font-size:13px;">当前筛选条件下没有匹配的数据资产。</div></div>`}
    `;
    $('mainContent').innerHTML = html;
    $('dmCatalogSearch').addEventListener('click', () => {
      state.catalogKeyword = $('dmCatalogKeyword').value.trim();
      renderCatalogView().catch(e => showToast(e.message, true));
    });
    $('dmCatalogKeyword').addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        state.catalogKeyword = $('dmCatalogKeyword').value.trim();
        renderCatalogView().catch(err => showToast(err.message, true));
      }
    });
  }

  function renderDynamicPlatform(p, color) {
    const ds = p.datasources || [];
    const syncedCount = ds.filter(d => ['success','empty'].includes(d.last_status)).length;
    let html = `
    <div class="mb-5">
      <div class="flex items-center gap-3 mb-1"><span class="w-8 h-8 rounded-lg bg-${color}-100 text-${color}-600 flex items-center justify-center flex-shrink-0">${platformIconHtml(p.name)}</span><h1 class="text-xl font-bold text-slate-800 tracking-tight">${escapeHtml(p.name)}</h1></div>
      <p class="text-sm text-slate-500">${escapeHtml(p.description || '暂无描述')}</p>
    </div>
    <div class="grid grid-cols-3 gap-3 mb-5">
      <div class="stat-card flex items-center gap-2.5"><div class="w-9 h-9 rounded-md bg-${color}-100 flex items-center justify-center text-${color}-600 text-base">⚡</div><div><div class="text-lg font-bold text-slate-800">${ds.length}</div><div class="text-xs text-slate-400">数据接口</div></div></div>
      <div class="stat-card flex items-center gap-2.5"><div class="w-9 h-9 rounded-md bg-${color}-100 flex items-center justify-center text-${color}-600 text-base">📊</div><div><div class="text-lg font-bold text-slate-800">${ds.reduce((s,d)=>s+(d.row_count||0),0).toLocaleString()}</div><div class="text-xs text-slate-400">已同步数据</div></div></div>
      <div class="stat-card flex items-center gap-2.5"><div class="w-9 h-9 rounded-md bg-${color}-100 flex items-center justify-center text-${color}-600 text-base">✅</div><div><div class="text-lg font-bold text-slate-800">${syncedCount}/${ds.length}</div><div class="text-xs text-slate-400">同步正常</div></div></div>
    </div>`;

    if (ds.length === 0) {
      html += `<div style="text-align:center;padding:60px 0;color:#94a3b8;">
        <div style="font-size:40px;margin-bottom:12px;">🔌</div>
        <p style="font-size:14px;">该平台暂无数据源</p>
        <p style="font-size:12px;margin-top:4px;">如需要本平台接口，请联系 AI 小组管理员</p>
      </div>`;
    } else {
      html += `<div class="grid grid-cols-1 xl:grid-cols-2 gap-3">`;
      ds.forEach(d => {
        const statusDot = ['success','empty'].includes(d.last_status) ? 'online' : (d.last_status ? 'degraded' : 'offline');
        const statusText = d.last_status === 'success' ? '● 在线' : (d.last_status === 'empty' ? '● 无数据' : (d.last_status === 'warning' ? '◐ 降级' : (d.last_status === 'failed' ? '◐ 异常' : '○ 未同步')));
        const fieldList = Object.entries(d.field_labels || {}).map(([k,v]) => `<span class="field-tag">${v}(${k})</span>`).join('');
        html += `
        <div class="api-card" id="card-${d.source_key}">
          <div class="p-3.5 flex items-start justify-between cursor-pointer" onclick="openDsDetail('${d.source_key}')">
            <div class="flex-1">
              <div class="flex items-center gap-2 mb-1.5">
                <h3 class="text-sm font-semibold text-slate-800">${escapeHtml(d.source_name)}</h3>
                <span class="text-xs flex items-center gap-1"><span class="pulse-dot ${statusDot}"></span>${statusText}</span>
                ${d.http_method ? `<span class="text-xs px-1.5 py-0.5 rounded ${d.http_method==='POST'?'method-post':'method-get'} font-mono">${d.http_method}</span>` : ''}
              </div>
              <p class="text-xs text-slate-500 mb-1.5">${escapeHtml(d.description || d.source_name)}</p>
              <div class="flex items-center gap-2 text-xs text-slate-400">
                <code class="text-xs bg-slate-100 px-1.5 py-0.5 rounded text-slate-600" title="${escapeHtml(d.api_url || '')}">${escapeHtml(d.source_key)}</code>
                ${d.row_count ? `<span>| ${d.row_count.toLocaleString()} 条</span>` : ''}
                ${d.last_sync_at ? `<span>| 同步于 ${d.last_sync_at}</span>` : ''}
              </div>
            </div>
            <div class="ml-3 mt-1 text-slate-400" style="font-size:13px;white-space:nowrap;">查看详情 →</div>
          </div>
        </div>`;
      });
      html += `</div>`;
    }
    $('mainContent').innerHTML = html;
  }

  // ======== 接口详情弹窗 ========
  let _currentDsDetail = null;

  window.openDsDetail = function(sourceKey) {
    // 从 state 里找到数据源对象
    let ds = null;
    for (const p of state.dynamicPlatforms) {
      ds = (p.datasources || []).find(d => d.source_key === sourceKey);
      if (ds) break;
    }
    if (!ds) {
      ds = (state.catalogItems || []).find(d => d.source_key === sourceKey) || null;
    }
    if (!ds) return;
    _currentDsDetail = ds;

    const statusDot = ['success','empty'].includes(ds.last_status) ? 'online' : (ds.last_status ? 'degraded' : 'offline');
    const statusText = ds.last_status === 'success' ? '在线' : (ds.last_status === 'empty' ? '无数据' : (ds.last_status === 'warning' ? '降级' : (ds.last_status === 'failed' ? '异常' : '未同步')));
    const isAdmin = !!(state.user && state.user.role === 'admin');
    const isLoggedIn = !!state.user;
    const hasPerm = isAdmin || ds.has_permission !== false;

    $('dmDdTitle').textContent = ds.source_name;
    if (ds.http_method) {
      $('dmDdMethod').textContent = ds.http_method;
      $('dmDdMethod').style.display = 'inline-flex';
      $('dmDdMethod').className = ds.http_method === 'POST' ? 'method-post' : 'method-get';
    } else {
      $('dmDdMethod').textContent = '';
      $('dmDdMethod').style.display = 'none';
      $('dmDdMethod').className = '';
    }
    $('dmDdStatus').innerHTML = `<span class="text-xs flex items-center gap-1" style="display:inline-flex;align-items:center;gap:4px;"><span class="pulse-dot ${statusDot}"></span>${statusText}</span>`;
    $('dmDdDesc').textContent = ds.description || '';
    $('dmDdEditBtn').classList.toggle('dm-hidden', !isAdmin);
    $('dmDdMcpBtn').classList.add('dm-hidden');
    $('dmDdMcpHttpBtn').classList.toggle('dm-hidden', !isLoggedIn);
    // $('dmDdDocBtn').classList.toggle('dm-hidden', !isLoggedIn);  // 功能保留，暂时隐藏
    if (hasPerm) {
      $('dmDdMcpHttpBtn').disabled = false;
      $('dmDdMcpHttpBtn').textContent = 'MCP(HTTP) 导出';
      $('dmDdMcpHttpBtn').title = '导出当前数据源的 MCP Streamable HTTP 配置';
      $('dmDdMcpHttpBtn').style.opacity = '1';
      $('dmDdMcpHttpBtn').style.cursor = 'pointer';
    } else if (isLoggedIn) {
      $('dmDdMcpHttpBtn').disabled = false;
      $('dmDdMcpHttpBtn').textContent = '申请 MCP(HTTP) 导出';
      $('dmDdMcpHttpBtn').title = '申请该数据源的 MCP(HTTP) 导出权限';
      $('dmDdMcpHttpBtn').style.opacity = '1';
      $('dmDdMcpHttpBtn').style.cursor = 'pointer';
      refreshMcpApplyButtonState(ds.source_key);
    }

    const fieldMeta = Array.isArray(ds.field_meta) && ds.field_meta.length
      ? ds.field_meta
      : Object.entries(ds.field_labels || {}).map(([k, v]) => ({
          field_name: k,
          field_label: v,
          standard_field_code: '',
          standard_field_name: v,
          data_type: 'text',
          entity_role: '',
          sensitivity_level: 'normal',
          business_domain: '',
          definition: ''
        }));
    const visibleFieldMeta = fieldMeta.filter(item => {
      const fieldName = String(item.field_name || '').trim();
      const fieldLabel = String(item.field_label || '').trim();
      return !!fieldName && !!fieldLabel && fieldLabel !== fieldName;
    });
    const fieldList = visibleFieldMeta.map(item => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;background:linear-gradient(90deg,#dbeafe 0%,#eff6ff 100%);border-left:4px solid #2563eb;">
        <span style="font-size:12px;font-weight:700;color:#1d4ed8;line-height:1.4;">${escapeHtml(item.field_label)}</span>
        <span style="font-size:11px;color:#64748b;font-family:monospace;line-height:1.4;word-break:break-all;">${escapeHtml(item.field_name)}</span>
      </div>`).join('');
    const platName = (() => {
      if (!ds.platform_id) return '—';
      const p = state.dynamicPlatforms.find(p => p.id === ds.platform_id);
      return p ? p.name : '—';
    })();

    // 左栏：字段说明
    $('dmDdFieldPanel').innerHTML = fieldList
      ? `<div style="font-size:11px;color:#94a3b8;font-weight:600;letter-spacing:.5px;text-transform:uppercase;margin-bottom:12px;">OneData 字段标准</div>
         <div style="display:flex;flex-direction:column;gap:8px;">${fieldList}</div>`
       : `<div style="color:#94a3b8;font-size:13px;">暂无可展示的中文字段标注</div>`;

    const parameterDocsHtml = renderParameterDocs(ds.request_config || {});

    // 右栏：数据预览
    $('dmDsDetailBody').innerHTML = `
      ${parameterDocsHtml}
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
        <span style="font-size:14px;font-weight:600;color:#1e293b;">数据预览</span>
        <button class="dm-btn ${hasPerm?'primary':'secondary'}" style="font-size:12px;padding:5px 14px;" onclick="loadDetailData('${escapeHtml(ds.source_key)}')">
          ${hasPerm ? '加载数据（前 20 条）' : '查看示例数据（5 条）'}
        </button>
      </div>
      <div id="dmDdDataZone" style="color:#94a3b8;font-size:13px;">点击上方按钮加载</div>
      ${isAdmin ? `
      <div style="margin-top:18px;border-top:1px solid #e2e8f0;padding-top:16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">
          <span style="font-size:14px;font-weight:600;color:#1e293b;">历史版本</span>
          <button class="dm-btn" style="font-size:12px;padding:5px 12px;" onclick="loadDetailVersions('${escapeHtml(ds.source_key)}')">刷新版本</button>
        </div>
        <div id="dmDdVersionZone" style="color:#94a3b8;font-size:13px;">正在加载历史版本...</div>
      </div>` : ''}`;

    $('dmDsDetailMask').classList.remove('dm-hidden');
    if (isAdmin) loadDetailVersions(ds.source_key);
  };

  async function refreshMcpApplyButtonState(sourceKey) {
    if (!_currentDsDetail || _currentDsDetail.source_key !== sourceKey) return;
    const httpBtn = $('dmDdMcpHttpBtn');
    try {
      const r = await apiFetch('/api/mcp/export-request/status?source_key=' + encodeURIComponent(sourceKey));
      if (!_currentDsDetail || _currentDsDetail.source_key !== sourceKey) return;
      if (r.has_permission) {
        _currentDsDetail.has_permission = true;
        httpBtn.disabled = false;
        httpBtn.textContent = 'MCP(HTTP) 导出';
        httpBtn.title = '导出当前数据源的 MCP Streamable HTTP 配置';
        httpBtn.style.opacity = '1';
        httpBtn.style.cursor = 'pointer';
        return;
      }
      const latest = r.latest;
      if (latest && latest.status === 'pending') {
        httpBtn.disabled = true;
        httpBtn.textContent = '申请处理中';
        httpBtn.title = '申请已提交，等待管理员审批';
        httpBtn.style.opacity = '.6';
        httpBtn.style.cursor = 'not-allowed';
      } else {
        httpBtn.disabled = false;
        httpBtn.textContent = '申请 MCP(HTTP) 导出';
        httpBtn.style.opacity = '1';
        httpBtn.style.cursor = 'pointer';
        httpBtn.title = (latest && latest.status === 'rejected' && latest.admin_comment)
          ? ('上次申请被驳回：' + latest.admin_comment + '（可再次申请）')
          : '申请该数据源的 MCP(HTTP) 导出权限';
      }
    } catch (e) { /* 静默失败，保留默认申请态 */ }
  }

  let _mcpApplySource = null;
  function openMcpApplyModal(ds) {
    _mcpApplySource = ds;
    $('dmMcpApplySourceName').textContent = `数据源：${ds.source_name}`;
    $('dmMcpApplyReason').value = '';
    $('dmMcpApplyMask').classList.remove('dm-hidden');
  }
  $('dmMcpApplyClose').addEventListener('click', () => $('dmMcpApplyMask').classList.add('dm-hidden'));
  $('dmMcpApplyCancel').addEventListener('click', () => $('dmMcpApplyMask').classList.add('dm-hidden'));
  $('dmMcpApplyMask').addEventListener('click', e => { if (e.target === $('dmMcpApplyMask')) $('dmMcpApplyMask').classList.add('dm-hidden'); });
  $('dmMcpApplySubmit').addEventListener('click', async () => {
    if (!_mcpApplySource) return;
    const reason = ($('dmMcpApplyReason').value || '').trim();
    if (reason.length < 2) { showToast('请填写至少 2 个字的申请原因', true); return; }
    try {
      const resp = await apiFetch('/api/mcp/export-request', {
        method: 'POST',
        body: JSON.stringify({ source_key: _mcpApplySource.source_key, reason }),
      });
      showToast(resp.message === 'Request already pending' ? '已有申请正在处理中' : '申请已提交，请等待管理员审批');
      $('dmMcpApplyMask').classList.add('dm-hidden');
      if (_currentDsDetail && _currentDsDetail.source_key === _mcpApplySource.source_key) {
        refreshMcpApplyButtonState(_mcpApplySource.source_key);
      }
    } catch (e) { showToast(e.message, true); }
  });

  window.loadDetailData = async function(sourceKey, asOf='', syncVersion='') {
    const el = $('dmDdDataZone');
    if (!el) return;
    el.innerHTML = '<span style="color:#64748b;font-size:13px;">加载中...</span>';
    try {
      const qs = new URLSearchParams({page:'1',page_size:'20'});
      if (asOf) qs.set('as_of', asOf);
      if (syncVersion) qs.set('sync_version', syncVersion);
      const r = await apiFetch(`/api/data/${sourceKey}?` + qs.toString());
      const rows = r.rows || [];
      if (!rows.length) { el.innerHTML = '<p style="color:#94a3b8;font-size:13px;">暂无数据</p>'; return; }
      const cols = r.columns && r.columns.length ? r.columns : Object.keys(rows[0]).filter(k=>!['id','sync_batch_id','synced_at','sync_version','is_current'].includes(k));
      const fieldLabels = r.field_labels || {};
      const previewBanner = r.preview_only ? `
        <div style="display:flex;align-items:center;gap:8px;background:#fefce8;border:1px solid #fde047;border-radius:8px;padding:10px 14px;margin-bottom:12px;">
          <span style="font-size:16px;">🔒</span>
          <div>
            <span style="font-size:13px;font-weight:600;color:#854d0e;">仅显示演示数据</span>
            <span style="font-size:12px;color:#a16207;margin-left:8px;">联系管理员申请完整访问权限</span>
          </div>
          <span style="margin-left:auto;font-size:11px;color:#a16207;">演示预览</span>
        </div>` : '';
      const versionBanner = (!r.preview_only && (r.effective_sync_version || r.as_of)) ? `
        <div style="display:flex;align-items:center;gap:8px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px 14px;margin-bottom:12px;">
          <span style="font-size:16px;">🕘</span>
          <div style="font-size:12px;color:#1d4ed8;">
            <div>版本：${escapeHtml(r.effective_sync_version || 'current')}</div>
            <div>时间：${escapeHtml(r.effective_finished_at || r.last_sync_at || '')}</div>
          </div>
        </div>` : '';
      el.innerHTML = `${previewBanner}${versionBanner}
        <div style="overflow-x:auto;border-radius:8px;border:1px solid #e2e8f0;">
          <table class="data-table">
            <thead><tr>${cols.map(c=>`<th>${escapeHtml(fieldLabels[c]||c)}</th>`).join('')}</tr></thead>
            <tbody>${rows.map(row=>`<tr>${cols.map(c=>`<td>${escapeHtml(String(row[c]??''))}</td>`).join('')}</tr>`).join('')}</tbody>
          </table>
        </div>
        ${!r.preview_only ? `<p style="color:#94a3b8;font-size:11px;margin-top:8px;">共 ${(r.total||rows.length).toLocaleString()} 条记录，当前显示前 20 条</p>` : ''}`;
    } catch(e) { el.innerHTML = `<p style="color:#ef4444;font-size:13px;">加载失败: ${escapeHtml(e.message)}</p>`; }
  };

  window.loadDetailVersions = async function(sourceKey) {
    const el = $('dmDdVersionZone');
    if (!el) return;
    el.innerHTML = '<span style="color:#64748b;font-size:13px;">加载中...</span>';
    try {
      const r = await apiFetch(`/api/admin/datasource/${sourceKey}/versions?limit=20`);
      const items = r.items || [];
      if (!items.length) {
        el.innerHTML = '<p style="color:#94a3b8;font-size:13px;">暂无历史版本</p>';
        return;
      }
      el.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px;">${items.map(v => `
        <div style="border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;background:${v.is_current ? '#f0fdf4' : '#fff'};">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span style="font-size:12px;font-weight:600;color:#0f172a;">${escapeHtml(v.sync_version)}</span>
            ${v.is_current ? '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#dcfce7;color:#166534;">当前</span>' : ''}
            <span style="font-size:11px;color:#64748b;">${escapeHtml(v.finished_at || '')}</span>
            <span style="font-size:11px;color:#64748b;">${escapeHtml(v.status || '')}</span>
            <span style="font-size:11px;color:#64748b;">${(v.row_count || 0).toLocaleString()} 条</span>
            <div style="margin-left:auto;display:flex;gap:6px;">
              <button class="dm-tbl-btn" onclick="loadDetailData('${escapeHtml(sourceKey)}','','${escapeHtml(v.sync_version)}')">查看</button>
              ${v.is_current ? '' : `<button class="dm-tbl-btn" style="color:#b45309;border-color:#fcd34d;" onclick="rollbackDatasourceVersion('${escapeHtml(sourceKey)}','${escapeHtml(v.sync_version)}')">回滚</button>`}
            </div>
          </div>
          ${v.message ? `<div style="margin-top:6px;font-size:11px;color:#64748b;">${escapeHtml(v.message)}</div>` : ''}
        </div>`).join('')}</div>`;
    } catch(e) {
      el.innerHTML = `<p style="color:#ef4444;font-size:13px;">加载失败: ${escapeHtml(e.message)}</p>`;
    }
  };

  window.rollbackDatasourceVersion = async function(sourceKey, syncVersion) {
    if (!confirm(`确认回滚到版本 ${syncVersion} 吗？`)) return;
    try {
      await apiFetch(`/api/admin/datasource/${sourceKey}/rollback`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({sync_version: syncVersion}),
      });
      showToast('版本已回滚');
      await loadDynamicPlatforms();
      await loadDetailVersions(sourceKey);
      await loadDetailData(sourceKey, '', syncVersion);
    } catch(e) {
      showToast(e.message, true);
    }
  };

  $('dmDsDetailClose').addEventListener('click', () => $('dmDsDetailMask').classList.add('dm-hidden'));
  $('dmDsDetailMask').addEventListener('click', e => { if (e.target === $('dmDsDetailMask')) $('dmDsDetailMask').classList.add('dm-hidden'); });

  $('dmDdEditBtn').addEventListener('click', () => {
    $('dmDsDetailMask').classList.add('dm-hidden');
    openDsEdit(_currentDsDetail);
  });
  $('dmDdMcpBtn').addEventListener('click', () => {
    if (!_currentDsDetail) return;
    const btn = $('dmDdMcpBtn');
    const isAdmin = !!(state.user && state.user.role === 'admin');
    const hasPerm = isAdmin || _currentDsDetail.has_permission !== false;
    if (hasPerm) { exportMcp(_currentDsDetail.source_key); return; }
    if (btn.disabled) { showToast('申请正在审批中，请耐心等待', true); return; }
    openMcpApplyModal(_currentDsDetail);
  });

  $('dmDdMcpHttpBtn').addEventListener('click', () => {
    if (!_currentDsDetail) return;
    const btn = $('dmDdMcpHttpBtn');
    const isAdmin = !!(state.user && state.user.role === 'admin');
    const hasPerm = isAdmin || _currentDsDetail.has_permission !== false;
    if (hasPerm) { exportMcpHttp(_currentDsDetail.source_key); return; }
    if (btn.disabled) { showToast('申请正在审批中，请耐心等待', true); return; }
    openMcpApplyModal(_currentDsDetail);
  });

  $('dmDdDocBtn').addEventListener('click', async () => {
    if (!_currentDsDetail) return;
    if (!state.user) { showToast('请先登录', true); $('dmLoginMask').classList.remove('dm-hidden'); return; }
    const sourceKey = _currentDsDetail.source_key;
    $('dmDocTitle').textContent = '接口文档';
    $('dmDocSubtitle').textContent = '可直接复制全文，或粘贴到文档中使用。';
    $('dmDocBody').innerHTML = `
      <div style="border:1px solid #dbeafe;border-radius:12px;background:#f8fbff;padding:16px;">
        <p style="color:#64748b;font-size:13px;">正在加载接口文档...</p>
      </div>`;
    $('dmDocMask').classList.remove('dm-hidden');
    try {
      const resp = await fetch(API_BASE + '/api/mcp/doc/' + encodeURIComponent(sourceKey), {
        headers: { 'Authorization': 'Bearer ' + state.token }
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `加载失败: HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const markdown = String(data.markdown || '');
      const tokenHint = data.mcp_token
        ? `<div style="margin-bottom:12px;border:1px solid #bbf7d0;border-radius:10px;background:#f0fdf4;padding:12px 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <span style="font-size:13px;color:#166534;font-weight:600;">✓ 已自动生成 MCP 令牌</span>
            <span style="font-size:12px;color:#64748b;">到期时间：${escapeHtml(data.expires_at || '—')}</span>
            <span style="font-size:12px;color:#94a3b8;margin-left:auto;">令牌已写入下方文档中，可复制直接使用</span>
          </div>`
        : '';
      $('dmDocTitle').textContent = (data.source_name || sourceKey) + ' · 接口文档';
      $('dmDocBody').innerHTML = `
        ${tokenHint}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-bottom:12px;">
          <button class="dm-btn primary" id="dmDocCopy" type="button">复制全文</button>
        </div>
        <div style="border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;padding:14px;overflow:auto;max-height:min(60vh,520px);">
          <pre id="dmDocPre" style="color:#334155;font-size:12px;margin:0;white-space:pre-wrap;word-break:break-all;font-family:monospace;line-height:1.6;">${escapeHtml(markdown)}</pre>
        </div>`;
      $('dmDocCopy').addEventListener('click', () => {
        navigator.clipboard.writeText(markdown).then(() => showToast('已复制到剪贴板'), () => showToast('复制失败', true));
      });
    } catch (e) {
      $('dmDocBody').innerHTML = `
        <div style="border:1px solid #fecaca;border-radius:12px;background:#fef2f2;padding:16px;">
          <p style="color:#b91c1c;font-size:13px;margin:0;">${escapeHtml(e.message)}</p>
        </div>`;
    }
  });

  $('dmDocClose').addEventListener('click', () => $('dmDocMask').classList.add('dm-hidden'));
  $('dmDocMask').addEventListener('click', e => { if (e.target === $('dmDocMask')) $('dmDocMask').classList.add('dm-hidden'); });

  // ======== 接口编辑弹窗 ========
  let _currentDsEdit = null;
  let _currentDsDelete = null;

  function openDsEdit(ds) {
    _currentDsEdit = ds;
    $('dmDeSubtitle').textContent = ds.source_name || ds.source_key;

    // 只读信息块
    $('dmDeInfoKey').textContent = ds.source_key || '—';
    $('dmDeInfoTable').textContent = ds.table_name || '—';
    $('dmDeInfoSync').textContent = ds.last_sync_at
      ? (ds.last_sync_at + (ds.last_status === 'success' ? ' ✅' : ds.last_status === 'empty' ? ' ∅' : ds.last_status === 'warning' ? ' ⚠️' : ds.last_status === 'failed' ? ' ❌' : ''))
      : '从未同步';
    $('dmDeInfoToken').textContent = ds.has_token ? '✔ 已配置' : '未配置';
    $('dmDeInfoToken').style.color = ds.has_token ? '#16a34a' : '#94a3b8';

    // 可编辑字段
    $('dmDeSourceName').value = ds.source_name || '';
      $('dmDeMethod').value = ds.http_method || 'POST';
      $('dmDeUrl').value = ds.api_url || '';
      $('dmDeToken').value = '';
      $('dmDeToken').type = 'password';
      $('dmDeTokenToggle').textContent = '显示';
      $('dmDeDesc').value = ds.description || '';
      $('dmDeSearchable').value = (ds.searchable_fields || []).join(', ');
      $('dmDeQualityRules').value = prettyJson(ds.quality_rules || {});
      $('dmDeVerifyTls').checked = !!ds.verify_tls;
      $('dmDeReqCfg').value = prettyJson(ds.request_config || {});
      $('dmDeRespCfg').value = prettyJson(ds.response_config || {});

    // 填充平台下拉
    const sel = $('dmDePlatform');
    sel.innerHTML = '<option value="">— 不指定 —</option>' +
      state.dynamicPlatforms.map(p => `<option value="${p.id}"${ds.platform_id === p.id ? ' selected' : ''}>${escapeHtml(p.name)}</option>`).join('');

    $('dmDsEditMask').classList.remove('dm-hidden');
  }

  function closeDatasourceDeleteModal() {
    $('dmDsDeleteMask').classList.add('dm-hidden');
    $('dmDsDeletePassword').value = '';
    _currentDsDelete = null;
  }

  function openDatasourceDeleteModal(ds) {
    if (!ds) return;
    _currentDsDelete = ds;
    $('dmDsDeleteSubtitle').textContent = `${ds.source_name || ds.source_key} (${ds.source_key})`;
    $('dmDsDeletePassword').value = '';
    $('dmDsDeleteMask').classList.remove('dm-hidden');
    setTimeout(() => $('dmDsDeletePassword').focus(), 0);
  }

  $('dmDsEditClose').addEventListener('click', () => $('dmDsEditMask').classList.add('dm-hidden'));
  $('dmDeCancel').addEventListener('click', () => $('dmDsEditMask').classList.add('dm-hidden'));
  $('dmDsEditMask').addEventListener('click', e => { if (e.target === $('dmDsEditMask')) $('dmDsEditMask').classList.add('dm-hidden'); });
  $('dmDeTokenToggle').addEventListener('click', () => {
    const input = $('dmDeToken');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    $('dmDeTokenToggle').textContent = show ? '隐藏' : '显示';
  });
  $('dmDeDelete').addEventListener('click', () => {
    if (_currentDsEdit) openDatasourceDeleteModal(_currentDsEdit);
  });
  $('dmDsDeleteClose').addEventListener('click', closeDatasourceDeleteModal);
  $('dmDsDeleteCancel').addEventListener('click', closeDatasourceDeleteModal);
  $('dmDsDeleteMask').addEventListener('click', e => { if (e.target === $('dmDsDeleteMask')) closeDatasourceDeleteModal(); });
  $('dmDsDeleteConfirm').addEventListener('click', async () => {
    if (!_currentDsDelete) return;
    const adminPassword = $('dmDsDeletePassword').value;
    if (!adminPassword) {
      showToast('请输入管理员密码', true);
      $('dmDsDeletePassword').focus();
      return;
    }
    try {
      const resp = await apiFetch(`/api/admin/datasource/${_currentDsDelete.id}/delete`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({admin_password: adminPassword}),
      });
      showToast(resp.message || '数据源已删除');
      closeDatasourceDeleteModal();
      $('dmDsEditMask').classList.add('dm-hidden');
      await loadDynamicPlatforms();
      renderAdminDatasources();
    } catch (e) {
      showToast(e.message, true);
    }
  });

  window.handleDatasourceSave = async function() {
    if (!_currentDsEdit) return;
    const saveBtn = $('dmDeSave');
    if (saveBtn.disabled) return;
    const platformVal = $('dmDePlatform').value;
    const tokenVal = $('dmDeToken').value.trim();
    const body = {
      source_name: $('dmDeSourceName').value.trim(),
      http_method: $('dmDeMethod').value,
      api_url: $('dmDeUrl').value.trim(),
      platform_id: platformVal ? parseInt(platformVal) : 0,
      description: $('dmDeDesc').value.trim(),
      searchable_fields: parseCommaList($('dmDeSearchable').value),
      quality_rules: parseJsonInput($('dmDeQualityRules').value, '质量规则'),
      verify_tls: $('dmDeVerifyTls').checked,
      request_config: parseJsonInput($('dmDeReqCfg').value, '请求配置'),
      response_config: parseJsonInput($('dmDeRespCfg').value, '响应配置'),
    };
    if (tokenVal === 'clear') body.token = '';
    else if (tokenVal) body.token = tokenVal;
    const oldLabel = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中...';
    try {
      await apiFetch('/api/admin/datasource/' + _currentDsEdit.id, {method:'PUT', body: JSON.stringify(body)});
      showToast('保存成功');
      $('dmDsEditMask').classList.add('dm-hidden');
      await loadDynamicPlatforms();
      // 如果当前在数据源管理页，也刷新
      if (currentAdminView === 'datasources') await renderAdminDatasources();
    } catch(e) { showToast(e.message, true); }
    finally {
      saveBtn.disabled = false;
      saveBtn.textContent = oldLabel;
    }
  };

  // 管理后台的"编辑"按钮也走同一个弹窗
  window._openDsEditById = function(dsId) {
    // 优先从 admin 缓存中取（数据最全，含未分配平台的数据源）
    if (window._adminDsMap && window._adminDsMap[dsId]) {
      openDsEdit(window._adminDsMap[dsId]);
      return;
    }
    // fallback: 从动态平台列表中找
    for (const p of state.dynamicPlatforms) {
      const ds = (p.datasources || []).find(d => d.id === dsId);
      if (ds) { openDsEdit(ds); return; }
    }
    showToast('请先打开「数据源管理」页面再点编辑', true);
  };

  // ======== MCP 导出 ========
  async function exportMcp(sourceRef) {
    if (!state.user) { showToast('请先登录', true); $('dmLoginMask').classList.remove('dm-hidden'); return; }
    const sourceKey = ERP_SOURCE_MAP[sourceRef] || sourceRef;
    if (!sourceKey) { showToast('该接口暂不支持 MCP 导出', true); return; }
    $('dmMcpBody').innerHTML = '<div style="border:1px solid #dbeafe;border-radius:12px;background:#f8fbff;padding:16px;margin:0;"><p style="color:#64748b;font-size:13px;">正在生成 MCP 令牌...</p></div>';
    $('dmMcpMask').classList.remove('dm-hidden');
    try {
      const data = await apiFetch('/api/mcp/export', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({source_keys:[sourceKey]}),
      });
      $('dmMcpBody').innerHTML = `
        <div style="border:1px solid #bbf7d0;border-radius:12px;background:#f0fdf4;padding:16px;margin-bottom:12px;">
          <p style="color:#166534;font-size:13px;margin:0 0 4px;">令牌生成成功</p>
          <p style="color:#64748b;font-size:11px;margin:0;">部门: ${escapeHtml(data.department||'未设置')} · 数据源: ${escapeHtml(data.source_keys.join(', '))} · 到期: ${escapeHtml(data.expires_at || '90 天后')}</p>
          <p style="color:#94a3b8;font-size:11px;margin:6px 0 0;">令牌编号: ${escapeHtml(String(data.token_id || ''))} · 可在“我的 MCP 列表”中自行停用或删除；IP 仅用于审计，不再限制调用</p>
        </div>
        <div style="border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;padding:14px;overflow:auto;max-height:400px;">
          <pre style="color:#334155;font-size:11px;margin:0;white-space:pre-wrap;word-break:break-all;">${escapeHtml(data.config_json)}</pre>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;">
          <button class="dm-btn primary" onclick="navigator.clipboard.writeText(document.querySelector('#dmMcpBody pre').textContent);showToast('已复制到剪贴板')">复制 JSON</button>
        </div>`;
    } catch(err) {
      $('dmMcpBody').innerHTML = `
        <div style="border:1px solid #fecaca;border-radius:12px;background:#fef2f2;padding:16px;margin-bottom:12px;">
          <p style="color:#b91c1c;font-size:13px;margin:0;">${escapeHtml(err.message)}</p>
        </div>`;
    }
  }
  window.exportMcp = exportMcp;
  window.showToast = showToast;

  // ======== MCP(HTTP) 导出（Streamable HTTP） ========
  async function exportMcpHttp(sourceRef) {
    if (!state.user) { showToast('请先登录', true); $('dmLoginMask').classList.remove('dm-hidden'); return; }
    const sourceKey = ERP_SOURCE_MAP[sourceRef] || sourceRef;
    if (!sourceKey) { showToast('该接口暂不支持 MCP(HTTP) 导出', true); return; }
    $('dmMcpBody').innerHTML = '<div style="border:1px solid #dbeafe;border-radius:12px;background:#f8fbff;padding:16px;margin:0;"><p style="color:#64748b;font-size:13px;">正在生成 MCP(HTTP) 令牌...</p></div>';
    $('dmMcpMask').classList.remove('dm-hidden');
    try {
      const data = await apiFetch('/api/mcp/export-http', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({source_keys:[sourceKey]}),
      });
      $('dmMcpBody').innerHTML = `
        <div style="border:1px solid #bbf7d0;border-radius:12px;background:#f0fdf4;padding:16px;margin-bottom:12px;">
          <p style="color:#166534;font-size:13px;margin:0 0 4px;">MCP(HTTP) 令牌生成成功</p>
          <p style="color:#64748b;font-size:11px;margin:0;">部门: ${escapeHtml(data.department||'未设置')} · 数据源: ${escapeHtml(data.source_keys.join(', '))} · 到期: ${escapeHtml(data.expires_at || '90 天后')}</p>
          <p style="color:#94a3b8;font-size:11px;margin:6px 0 0;">令牌编号: ${escapeHtml(String(data.token_id || ''))} · 可在“我的 MCP 列表”中自行停用或删除；IP 仅用于审计，不再限制调用</p>
        </div>
        <div style="border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;padding:14px;overflow:auto;max-height:400px;">
          <pre style="color:#334155;font-size:11px;margin:0;white-space:pre-wrap;word-break:break-all;">${escapeHtml(data.config_json)}</pre>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;">
          <button class="dm-btn primary" onclick="navigator.clipboard.writeText(document.querySelector('#dmMcpBody pre').textContent);showToast('已复制到剪贴板')">复制 JSON</button>
        </div>`;
    } catch(err) {
      $('dmMcpBody').innerHTML = `
        <div style="border:1px solid #fecaca;border-radius:12px;background:#fef2f2;padding:16px;margin-bottom:12px;">
          <p style="color:#b91c1c;font-size:13px;margin:0;">${escapeHtml(err.message)}</p>
        </div>`;
    }
  }
  window.exportMcpHttp = exportMcpHttp;
  $('dmMcpClose').addEventListener('click', () => $('dmMcpMask').classList.add('dm-hidden'));

  // ======== 查看 MCP 原文 ========
  async function viewMcpOriginal(tokenId, isAdmin) {
    if (!state.user) { showToast('请先登录', true); return; }
    const endpoint = isAdmin
      ? '/api/admin/mcp-token/' + tokenId + '/config'
      : '/api/mcp/token/' + tokenId + '/config';
    $('dmMcpBody').innerHTML = '<div style="border:1px solid #dbeafe;border-radius:12px;background:#f8fbff;padding:16px;margin:0;"><p style="color:#64748b;font-size:13px;">正在加载 MCP 原文配置...</p></div>';
    $('dmMcpMask').classList.remove('dm-hidden');
    try {
      const data = await apiFetch(endpoint, {method:'POST'});
      const hasSse = !!data.config_json;
      const hasHttp = !!data.config_json_http;
      $('dmMcpBody').innerHTML = `
        <div style="border:1px solid #bbf7d0;border-radius:12px;background:#f0fdf4;padding:12px 16px;margin-bottom:12px;">
          <p style="color:#166534;font-size:13px;margin:0;">MCP 原文配置</p>
          <p style="color:#64748b;font-size:11px;margin:4px 0 0;">数据源: ${escapeHtml((data.source_keys||[]).join(', '))} · 状态: ${escapeHtml(data.status||'')} · 到期: ${escapeHtml(data.expires_at||'')}</p>
        </div>
        ${hasSse ? `
        <div style="margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <span style="font-size:12px;font-weight:600;color:#334155;">SSE 配置</span>
            <button class="dm-btn primary" onclick="navigator.clipboard.writeText(document.querySelector('#mcpOriginalSse').textContent);showToast('SSE 配置已复制')">复制 SSE</button>
          </div>
          <div style="border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;padding:14px;overflow:auto;max-height:260px;">
            <pre id="mcpOriginalSse" style="color:#334155;font-size:11px;margin:0;white-space:pre-wrap;word-break:break-all;">${escapeHtml(data.config_json)}</pre>
          </div>
        </div>` : ''}
        ${hasHttp ? `
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <span style="font-size:12px;font-weight:600;color:#334155;">HTTP (Streamable HTTP) 配置</span>
            <button class="dm-btn primary" onclick="navigator.clipboard.writeText(document.querySelector('#mcpOriginalHttp').textContent);showToast('HTTP 配置已复制')">复制 HTTP</button>
          </div>
          <div style="border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;padding:14px;overflow:auto;max-height:260px;">
            <pre id="mcpOriginalHttp" style="color:#334155;font-size:11px;margin:0;white-space:pre-wrap;word-break:break-all;">${escapeHtml(data.config_json_http)}</pre>
          </div>
        </div>` : ''}
        ${!hasSse && !hasHttp ? '<p style="color:#94a3b8;font-size:12px;">该令牌没有保存原文配置（可能是旧令牌或数据缺失）。</p>' : ''}`;
    } catch(err) {
      $('dmMcpBody').innerHTML = `
        <div style="border:1px solid #fecaca;border-radius:12px;background:#fef2f2;padding:16px;margin-bottom:12px;">
          <p style="color:#b91c1c;font-size:13px;margin:0;">${escapeHtml(err.message)}</p>
        </div>`;
    }
  }
  window.viewMcpOriginal = viewMcpOriginal;


  // ======== 管理员侧栏导航 ========
  document.getElementById('adminSideNav').addEventListener('click', async function(e) {
    const item = e.target.closest('.admin-nav-item');
    if (!item) return;
    document.querySelectorAll('#platNav .plat-item').forEach(el=>el.classList.remove('active'));
    document.querySelectorAll('#adminSideNav .admin-nav-item').forEach(el=>el.classList.remove('active'));
    document.querySelectorAll('#userSideNav .admin-nav-item').forEach(el=>el.classList.remove('active'));
    item.classList.add('active');
    currentAdminView = item.dataset.admin;
    expandedCard = null;
    await loadAdminData();
    renderAdminView(currentAdminView);
  });

  async function loadAdminData() {
    if (!state.user || state.user.role !== 'admin') return;
    try {
      const [stats, users, syncLog, auditLog] = await Promise.all([
        apiFetch('/api/admin/stats', {method:'POST'}),
        apiFetch('/api/admin/user/list', {method:'POST'}),
        apiFetch('/api/admin/sync-log?page=1&page_size=20', {method:'POST'}),
        apiFetch('/api/admin/audit-log?page=1&page_size=20&keyword='+encodeURIComponent(state.admin.auditKeyword||''), {method:'POST'}),
      ]);
      state.admin.stats = stats; state.admin.users = users.items||[]; state.admin.syncLogs = syncLog.items||[]; state.admin.auditLogs = auditLog.items||[];
    } catch(e) { showToast('加载管理数据失败: '+e.message, true); }
  }

  function renderAdminView(view) {
    if (!state.user || state.user.role !== 'admin') return;
    if (view !== 'sync' && _syncCountdownTimer) { clearInterval(_syncCountdownTimer); _syncCountdownTimer = null; }
    switch(view) {
      case 'users': renderAdminUsers(); break;
      case 'datasources': renderAdminDatasources(); break;
      case 'sync': renderAdminSync().catch(()=>{}); break;
      case 'mcp': renderAdminMcp().catch(()=>{}); break;
      case 'mcp_request': renderAdminMcpRequests().catch(()=>{}); break;
      case 'audit': renderAdminAudit(); break;
      default: currentAdminView = null; renderPlatform(currentPlat);
    }
  }

  function renderMetricCard(v,l) { return `<div class="dm-metric"><div class="value">${v}</div><div class="label">${l}</div></div>`; }

  function renderAdminUsers() {
    const users = state.admin.users;
    const html = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">用户管理</h1><p class="text-sm text-slate-500 mt-1">管理平台用户、分配数据源权限</p></div>
    <div class="admin-panel">
      <h3>新建用户</h3>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;">
        <input class="dm-input" id="dmNewEmpNo" placeholder="工号">
        <input class="dm-input" id="dmNewUsername" placeholder="用户名">
        <input class="dm-input" id="dmNewFullName" placeholder="姓名">
        <input class="dm-input" id="dmNewPassword" type="password" placeholder="密码(至少6位)">
        <input class="dm-input" id="dmNewDept" placeholder="部门">
        <select class="dm-input" id="dmNewRole"><option value="user">user</option><option value="admin">admin</option></select>
      </div>
      <button class="dm-btn primary" id="dmCreateUserBtn">创建用户</button>
    </div>
    <div class="admin-panel">
      <h3>用户列表（${users.length} 人）</h3>
      <div style="overflow:auto;"><table class="dm-table">
        <thead><tr><th>ID</th><th>工号</th><th>用户名</th><th>姓名</th><th>部门</th><th>角色</th><th>操作</th></tr></thead>
        <tbody>${users.map(u=>`<tr>
          <td>${u.id}</td><td>${escapeHtml(u.employee_no)}</td><td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.full_name)}</td>
          <td>${escapeHtml(u.department||'')}</td><td>${u.role}</td>
          <td style="display:flex;gap:6px;flex-wrap:wrap;">
            ${u.role!=='admin'?`<button class="dm-tbl-btn" data-perm-user="${u.id}" data-perm-username="${escapeHtml(u.username)}" data-perm-fullname="${escapeHtml(u.full_name)}" data-perm-dept="${escapeHtml(u.department||'')}">🔑 权限</button>`:''}
            <button class="dm-tbl-btn" data-reset-pwd-user="${u.id}" data-reset-pwd-username="${escapeHtml(u.username)}" data-reset-pwd-fullname="${escapeHtml(u.full_name)}">🔁 重置密码</button>
            <button class="dm-tbl-btn danger" data-del-user="${u.id}" data-del-username="${escapeHtml(u.username)}" data-del-fullname="${escapeHtml(u.full_name)}">🗑 删除</button>
          </td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>`;
    $('mainContent').innerHTML = html;
    // Bind events
    $('dmCreateUserBtn').addEventListener('click', async ()=>{
      try {
        const body = {employee_no:$('dmNewEmpNo').value.trim(), username:$('dmNewUsername').value.trim(),
          full_name:$('dmNewFullName').value.trim(), password:$('dmNewPassword').value,
          department:$('dmNewDept').value.trim(), role:$('dmNewRole').value};
        if (!body.username||!body.password||!body.employee_no||!body.full_name) return showToast('请填写完整信息',true);
        if (body.password.length < 6) return showToast('密码至少6位',true);
        await apiFetch('/api/admin/user/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        showToast('创建成功'); await loadAdminData(); renderAdminUsers();
      } catch(e) { showToast(e.message, true); }
    });
    // Permission buttons
    document.querySelectorAll('[data-perm-user]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const uid = btn.dataset.permUser;
        $('dmPermUserName').textContent = btn.dataset.permFullname + ' (' + btn.dataset.permUsername + ')';
        loadPermForm(uid);
        $('dmPermMask').classList.remove('dm-hidden');
      });
    });
    // Delete buttons
    document.querySelectorAll('[data-del-user]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        openUserDeleteModal({
          id: btn.dataset.delUser,
          username: btn.dataset.delUsername,
          full_name: btn.dataset.delFullname,
        });
      });
    });
    // Reset password buttons
    document.querySelectorAll('[data-reset-pwd-user]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        const uid = btn.dataset.resetPwdUser;
        const label = `${btn.dataset.resetPwdFullname || ''}（${btn.dataset.resetPwdUsername}）`;
        if (!confirm(`确认重置 ${label} 的密码？重置后旧密码立即失效，该用户下次登录会被要求设置新密码。`)) return;
        try {
          const resp = await apiFetch(`/api/admin/user/${uid}/reset-password`, {method:'POST'});
          window.prompt(`已重置，临时密码如下（请自行复制并告知本人，此窗口关闭后不会再显示）：`, resp.temp_password);
          showToast('密码已重置');
        } catch(e) { showToast(e.message, true); }
      });
    });
  }
  $('dmPermClose').addEventListener('click', () => $('dmPermMask').classList.add('dm-hidden'));

  let _currentUserDelete = null;
  function closeUserDeleteModal() {
    $('dmUserDeleteMask').classList.add('dm-hidden');
    $('dmUserDeletePassword').value = '';
    _currentUserDelete = null;
  }
  function openUserDeleteModal(u) {
    if (!u) return;
    _currentUserDelete = u;
    $('dmUserDeleteSubtitle').textContent = `${u.full_name || u.username} (${u.username})`;
    $('dmUserDeletePassword').value = '';
    $('dmUserDeleteMask').classList.remove('dm-hidden');
    setTimeout(() => $('dmUserDeletePassword').focus(), 0);
  }
  $('dmUserDeleteClose').addEventListener('click', closeUserDeleteModal);
  $('dmUserDeleteCancel').addEventListener('click', closeUserDeleteModal);
  $('dmUserDeleteMask').addEventListener('click', e => { if (e.target === $('dmUserDeleteMask')) closeUserDeleteModal(); });
  $('dmUserDeleteConfirm').addEventListener('click', async () => {
    if (!_currentUserDelete) return;
    const adminPassword = $('dmUserDeletePassword').value;
    if (!adminPassword) {
      showToast('请输入管理员密码', true);
      $('dmUserDeletePassword').focus();
      return;
    }
    try {
      const resp = await apiFetch(`/api/admin/user/${_currentUserDelete.id}/delete`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({admin_password: adminPassword}),
      });
      showToast(resp.message || '用户已删除');
      closeUserDeleteModal();
      await loadAdminData();
      renderAdminUsers();
    } catch (e) {
      showToast(e.message, true);
    }
  });

  async function loadPermForm(uid) {
    try {
      const [allDs, permData] = await Promise.all([
        apiFetch('/api/admin/datasource/list', {method:'POST'}),
        apiFetch('/api/admin/user/' + uid + '/permissions'),
      ]);
      const sources = (allDs.items||[]).filter(s=>s.enabled);
      const directPerms = new Set((permData.direct_source_keys||permData.source_keys||[]));
      const deptName = (permData.department || '').trim();
      const deptData = deptName ? await apiFetch('/api/admin/department-permissions?department=' + encodeURIComponent(deptName)) : {source_keys: []};
      const deptPerms = new Set((deptData.source_keys||permData.department_source_keys||[]));
      const effectivePerms = new Set((permData.effective_source_keys||[]));
      const origins = permData.permission_origins || {};

      let html = `
        <div style="margin-bottom:14px;">
          <label style="display:block;color:#475569;font-size:13px;margin-bottom:6px;font-weight:600;">部门</label>
          <input id="dmPermDeptInput" value="${escapeHtml(deptName)}" placeholder="填写部门后可配置部门继承权限" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;background:#fff;color:#0f172a;">
          <p style="margin:8px 0 0;color:#64748b;font-size:12px;">用户有效权限 = 用户直授权限 + 部门继承权限</p>
        </div>
        <div style="overflow:auto;border:1px solid #e2e8f0;border-radius:12px;background:#fff;">
          <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead style="background:#f8fafc;color:#64748b;">
              <tr>
                <th style="text-align:left;padding:10px 12px;">数据源</th>
                <th style="text-align:center;padding:10px 12px;">用户授权</th>
                <th style="text-align:center;padding:10px 12px;">部门授权</th>
                <th style="text-align:left;padding:10px 12px;">生效状态</th>
              </tr>
            </thead>
            <tbody>
      `;
      sources.forEach(s=>{
        const directChecked = directPerms.has(s.source_key) ? 'checked' : '';
        const deptChecked = deptPerms.has(s.source_key) ? 'checked' : '';
        const origin = origins[s.source_key] || (effectivePerms.has(s.source_key) ? 'user' : 'none');
        const originText = origin === 'both' ? '用户 + 部门' : origin === 'user' ? '用户授权' : origin === 'department' ? '部门授权' : '无权限';
        html += `
          <tr style="border-top:1px solid #e2e8f0;">
            <td style="padding:10px 12px;color:#0f172a;">
              <div>${escapeHtml(s.source_name)}</div>
              <div style="color:#94a3b8;font-size:12px;">${escapeHtml(s.source_key)}</div>
            </td>
            <td style="text-align:center;padding:10px 12px;">
              <input type="checkbox" class="dm-user-perm" value="${escapeHtml(s.source_key)}" ${directChecked} style="accent-color:#3b82f6;">
            </td>
            <td style="text-align:center;padding:10px 12px;">
              <input type="checkbox" class="dm-dept-perm" value="${escapeHtml(s.source_key)}" ${deptChecked} ${deptName ? '' : 'disabled'} style="accent-color:#f59e0b;">
            </td>
            <td style="padding:10px 12px;color:${origin === 'none' ? '#94a3b8' : '#0f172a'};">${originText}</td>
          </tr>
        `;
      });
      html += `
            </tbody>
          </table>
        </div>
        <div style="margin-top:14px;display:flex;gap:10px;justify-content:flex-end;">
          <button class="dm-btn primary" id="dmPermSave">保存权限</button>
        </div>
        <div id="dmUserFieldGrantBox"></div>
      `;
      $('dmPermBody').innerHTML = html;
      renderUserFieldGrants(uid, sources);
      $('dmPermSave').addEventListener('click', async ()=>{
        const deptVal = $('dmPermDeptInput').value.trim();
        const checked = [...$('dmPermBody').querySelectorAll('input.dm-user-perm:checked')].map(cb=>cb.value);
        const deptChecked = [...$('dmPermBody').querySelectorAll('input.dm-dept-perm:checked')].map(cb=>cb.value);
        try {
          if (deptVal) {
            await apiFetch('/api/admin/user/' + uid + '/department', {
              method:'PUT',
              headers:{'Content-Type':'application/json'},
              body:JSON.stringify({department:deptVal}),
            });
            await apiFetch('/api/admin/department-permissions', {
              method:'POST',
              headers:{'Content-Type':'application/json'},
              body:JSON.stringify({department:deptVal, source_keys:deptChecked}),
            });
          }
          await apiFetch('/api/admin/user/'+uid+'/permissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_keys:checked})});
          showToast('权限已更新'); $('dmPermMask').classList.add('dm-hidden'); await loadAdminData(); renderAdminUsers();
        } catch(e) { showToast(e.message, true); }
      });
    } catch(e) { $('dmPermBody').innerHTML = '<p style="color:#f87171;">加载失败: '+escapeHtml(e.message)+'</p>'; }
  }

  // ======== 数据源管理 ========
  async function renderAdminDatasources() {
    let sources = [], platforms = [], llmServices = [];
    try {
      const [dsR, ptR, llmR] = await Promise.all([
        apiFetch('/api/admin/datasource/list', {method:'POST'}),
        apiFetch('/api/admin/platform/list', {method:'POST'}),
        apiFetch('/api/admin/llm-service/list', {method:'POST'}).catch(() => ({items:[]})),
      ]);
      sources = dsR.items||[];
      platforms = ptR.items||[];
      llmServices = llmR.items||[];
    } catch(e) { showToast(e.message,true); }

    const platformOpts = platforms.map(p=>`<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
    const preferredLlm = llmServices.find(s=>s.is_default && s.enabled) || llmServices.find(s=>s.enabled) || llmServices[0] || null;
    const llmServiceOpts = llmServices.map(s=>`<option value="${s.id}" ${preferredLlm && s.id===preferredLlm.id ? 'selected' : ''}>${escapeHtml(s.name)} · ${escapeHtml(s.model)}</option>`).join('');
    const html = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">数据源管理</h1><p class="text-sm text-slate-500 mt-1">集中管理平台、数据源配置与启停状态。</p></div>

    <div class="admin-panel">
      <h3 style="display:flex;align-items:center;gap:8px;">平台管理 <span style="font-size:11px;color:#94a3b8;font-weight:400;">可用上下按钮调整导航顺序。删除仅用于清理，已停用数据源会自动解除平台关联。</span></h3>
      <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:12px;" id="dmPtList">
        ${platforms.length===0?'<span style="color:#64748b;font-size:13px;">当前还没有平台，请先创建。</span>':platforms.map((p,idx)=>`
        <div class="dm-pt-row" data-pt-id="${p.id}" style="display:flex;align-items:center;gap:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:6px 10px;">
          <div style="display:flex;flex-direction:column;gap:1px;">
            <button class="dm-pt-up" data-pt-idx="${idx}" title="上移" style="background:none;border:none;color:#94a3b8;cursor:pointer;font-size:11px;line-height:1;padding:1px 3px;" ${idx===0?'disabled':''}>^</button>
            <button class="dm-pt-dn" data-pt-idx="${idx}" title="下移" style="background:none;border:none;color:#94a3b8;cursor:pointer;font-size:11px;line-height:1;padding:1px 3px;" ${idx===platforms.length-1?'disabled':''}>v</button>
          </div>
          <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;">
            <span style="font-size:13px;color:#3b82f6;font-weight:500;">${escapeHtml(p.name)}</span>
            <span style="font-size:11px;color:#94a3b8;">数据源: ${p.datasource_count||0}，启用中: ${p.active_datasource_count||0}</span>
          </div>
          <span style="font-size:11px;color:#94a3b8;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(p.description||'')}</span>
          <button class="dm-pt-edit" data-pt-id="${p.id}" data-pt-name="${escapeHtml(p.name)}" data-pt-desc="${escapeHtml(p.description||'')}" title="编辑" style="background:none;border:none;color:#93c5fd;cursor:pointer;font-size:13px;padding:2px 4px;">编辑</button>
          ${p.active_datasource_count>0
            ? `<button class="dm-pt-del" data-pt-id="${p.id}" data-pt-active="${p.active_datasource_count}" title="仍存在启用中的数据源" disabled style="background:none;border:none;color:#cbd5e1;cursor:not-allowed;font-size:13px;line-height:1;padding:2px 4px;opacity:.5;">删除</button>`
            : `<button class="dm-pt-del" data-pt-id="${p.id}" data-pt-inactive="${(p.datasource_count||0)-(p.active_datasource_count||0)}" title="${(p.datasource_count||0)>0?'删除平台并解除停用数据源关联':'删除空平台'}" style="background:none;border:none;color:#f87171;cursor:pointer;font-size:13px;line-height:1;padding:2px 4px;">删除</button>`}
        </div>`).join('')}
      </div>
      <div style="display:flex;gap:8px;">
        <input class="dm-input" id="dmPtName" placeholder="平台名称，如 ERP / SRM / WMS" style="max-width:220px;">
        <input class="dm-input" id="dmPtDesc" placeholder="平台描述（可选）" style="max-width:200px;">
        <button class="dm-btn primary" id="dmPtCreate">+ 新增平台</button>
      </div>
    </div>

    <div class="admin-panel">
      <h3>新增数据源</h3>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:10px;">
        <div style="grid-column:1/-1;border:1px solid #dbeafe;background:#f8fbff;border-radius:12px;padding:14px 14px 12px;">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px;">
            <div>
              <div style="font-size:14px;font-weight:700;color:#1d4ed8;">智能导入接口文档</div>
              <div style="font-size:12px;color:#64748b;margin-top:3px;">支持粘贴接口说明，或上传 txt / md 文本后用模型自动分析，并回填下面的数据源表单。</div>
            </div>
            <div style="font-size:11px;color:#94a3b8;">仅回填配置，不会直接创建数据源</div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:8px;">
            <select class="dm-input" id="dmAiService">
              <option value="">${llmServices.length ? '选择模型服务' : '请先在上方配置模型服务'}</option>
              ${llmServiceOpts}
            </select>
            <input class="dm-input" id="dmAiFileName" placeholder="文档名（可选）">
            <input class="dm-input" id="dmAiDocFile" type="file" accept=".txt,.md,.json,.csv,text/plain">
          </div>
          <textarea class="dm-input" id="dmAiDocText" rows="8" style="grid-column:1/-1;font-family:monospace;" placeholder="粘贴接口说明、请求参数、返回字段、示例 JSON 等文本"></textarea>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center;">
            <button class="dm-btn primary" id="dmAiParseDoc">解析文档并回填</button>
            <button class="dm-btn" id="dmAiClearDoc">清空文档</button>
            <span id="dmAiParseResult" style="font-size:12px;color:#64748b;">${llmServices.length ? '选择模型服务后即可开始解析。' : '当前还没有模型服务配置。'}</span>
          </div>
        </div>
        <input class="dm-input" id="dmDsKey" placeholder="source_key">
        <input class="dm-input" id="dmDsName" placeholder="显示名称">
        <input class="dm-input" id="dmDsTable" placeholder="存储表，如 ods_erp_xxx">
        <select class="dm-input" id="dmDsMethod"><option>POST</option><option>GET</option></select>
        <input class="dm-input" id="dmDsUrl" placeholder="接口 URL" style="grid-column:1/-1;">
        <input class="dm-input" id="dmDsToken" placeholder="Token（Bearer / Basic）" style="grid-column:1/-1;" type="password">
        <label style="grid-column:1/-1;display:flex;align-items:center;gap:8px;font-size:13px;color:#475569;">
          <input type="checkbox" id="dmDsVerifyTls" checked>
          验证 TLS 证书
        </label>
        <div style="grid-column:1/-1;display:flex;align-items:center;gap:8px;">
          <label style="font-size:13px;color:#64748b;white-space:nowrap;">所属平台</label>
          <select class="dm-input" id="dmDsPlatform" style="flex:1;max-width:220px;">
            <option value="">- 未分配 -</option>
            ${platformOpts}
          </select>
        </div>
        <input class="dm-input" id="dmDsDesc" placeholder="描述" style="grid-column:1/-1;">
        <input class="dm-input" id="dmDsSearchable" placeholder="可搜索字段，多个字段用逗号分隔" style="grid-column:1/-1;">
        <textarea class="dm-input" id="dmDsQualityRules" rows="5" style="grid-column:1/-1;font-family:monospace;" placeholder='质量规则 JSON'></textarea>
        <textarea class="dm-input" id="dmDsReqCfg" rows="7" style="grid-column:1/-1;font-family:monospace;" placeholder='请求配置 JSON'></textarea>
        <textarea class="dm-input" id="dmDsRespCfg" rows="6" style="grid-column:1/-1;font-family:monospace;" placeholder='响应配置 JSON'></textarea>
      </div>
      <div style="display:flex;gap:8px;">
        <button class="dm-btn primary" id="dmDsCreate">新增数据源</button>
        <button class="dm-btn" id="dmDsTest">测试连接</button>
      </div>
    </div>

    <div class="admin-panel">
      <h3>模型服务配置</h3>
      <p>这里配置 newapi 或其他 OpenAI 兼容模型服务，供“智能导入接口文档”调用。编辑已有配置时，API Key 留空表示保留原密钥。</p>
      <div style="display:grid;grid-template-columns:minmax(320px,420px) minmax(0,1fr);gap:14px;align-items:start;">
        <div style="border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;padding:14px;">
          <input type="hidden" id="dmLlmServiceId">
          <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;">
            <input class="dm-input" id="dmLlmName" placeholder="配置名称，如 newapi-正式">
            <input class="dm-input" id="dmLlmModel" placeholder="模型名，如 gpt-4o-mini">
            <input class="dm-input" id="dmLlmBaseUrl" placeholder="Base URL，如 https://xxx.com/v1" style="grid-column:1/-1;">
            <input class="dm-input" id="dmLlmApiKey" type="password" placeholder="API Key" style="grid-column:1/-1;">
          </div>
          <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:10px;font-size:12px;color:#475569;">
            <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="dmLlmEnabled" checked>启用</label>
            <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="dmLlmDefault">设为默认</label>
            <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="dmLlmVerifyTls" checked>验证 TLS</label>
            <label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="dmLlmShowKey">明文显示 Key</label>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;">
            <button class="dm-btn primary" id="dmLlmSave">新增模型服务</button>
            <button class="dm-btn" id="dmLlmReset">清空</button>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;">
          ${llmServices.length===0
            ? '<div style="border:1px dashed #cbd5e1;border-radius:12px;padding:18px;background:#fff;color:#64748b;font-size:13px;">还没有模型服务配置。先在左侧填写 base_url、model、API Key，保存后即可用于智能解析。</div>'
            : llmServices.map(s=>`
              <div style="display:flex;align-items:center;gap:10px;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px;background:#fff;">
                <div style="flex:1;min-width:0;">
                  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                    <span style="font-size:14px;font-weight:700;color:#0f172a;">${escapeHtml(s.name)}</span>
                    <span style="font-size:11px;padding:2px 8px;border-radius:999px;background:${s.enabled?'#dcfce7':'#e2e8f0'};color:${s.enabled?'#166534':'#475569'};">${s.enabled?'启用中':'已停用'}</span>
                    ${s.is_default?'<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#dbeafe;color:#1d4ed8;">默认</span>':''}
                  </div>
                  <div style="font-size:12px;color:#475569;margin-top:5px;">模型：${escapeHtml(s.model)} · TLS：${s.verify_tls?'验证':'跳过'}</div>
                  <div style="font-size:11px;color:#64748b;margin-top:4px;word-break:break-all;">${escapeHtml(s.base_url)}</div>
                  <div style="font-size:11px;color:#94a3b8;margin-top:4px;">密钥：${escapeHtml(s.api_key_masked || (s.has_api_key ? '已配置' : '未配置'))}</div>
                </div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
                  <button class="dm-tbl-btn" data-llm-edit="${s.id}">编辑</button>
                  <button class="dm-tbl-btn danger" data-llm-delete="${s.id}">删除</button>
                </div>
              </div>`).join('')}
        </div>
      </div>
    </div>

    <div class="admin-panel">
      <h3>已接入数据源（${sources.length}） <span style="font-size:11px;color:#94a3b8;font-weight:400;">停用后会从导航和自动同步中移除，但会保留配置、历史数据与版本记录。</span></h3>
      <div style="overflow:auto;"><table class="dm-table">
        <thead><tr><th>ID</th><th>标识</th><th>名称</th><th>平台</th><th>方法</th><th>当前版本</th><th>版本数</th><th>Token</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${sources.map(s=>`<tr>
          <td>${s.id}</td>
          <td><span title="${escapeHtml(s.api_url||'')}">${escapeHtml(s.source_key)}</span></td>
          <td>${escapeHtml(s.source_name)}</td>
          <td>${s.platform_name?`<span style="background:rgba(59,130,246,0.12);border-radius:4px;padding:2px 7px;font-size:12px;color:#60a5fa;">${escapeHtml(s.platform_name)}</span>`:'<span style="color:#475569;">-</span>'}</td>
          <td>${s.http_method}</td>
          <td style="font-size:12px;color:#475569;max-width:180px;">
            <span title="${escapeHtml(s.current_sync_version||'')}">${escapeHtml(s.current_sync_version||'-')}</span>
          </td>
          <td>${Number(s.version_count||0)}</td>
          <td>${s.has_token?'<span style="color:#34d399;font-size:12px;">已配置</span>':'<span style="color:#475569;font-size:12px;">-</span>'}</td>
          <td>${s.enabled?'启用':'停用'}</td>
          <td style="display:flex;gap:4px;flex-wrap:wrap;">
            <button class="dm-tbl-btn" data-ds-versions="${escapeHtml(s.source_key)}">版本</button>
            <button class="dm-tbl-btn" data-ds-edit="${s.id}">编辑</button>
            <button class="dm-tbl-btn danger" data-ds-delete="${s.id}">删除</button>
            ${s.enabled
              ? `<button class="dm-tbl-btn danger" data-ds-disable="${s.id}">停用</button>`
              : `<button class="dm-tbl-btn" data-ds-enable="${s.id}" style="color:#16a34a;border-color:#bbf7d0;">启用</button>`}
          </td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>`;
    $('mainContent').innerHTML = html;

    // Cache datasource objects for the edit modal.
    window._adminDsMap = {};
    sources.forEach(s => { window._adminDsMap[s.id] = s; });
    window._adminLlmServiceMap = {};
    llmServices.forEach(s => { window._adminLlmServiceMap[s.id] = s; });

    function resetLlmServiceForm() {
      $('dmLlmServiceId').value = '';
      $('dmLlmName').value = '';
      $('dmLlmModel').value = '';
      $('dmLlmBaseUrl').value = '';
      $('dmLlmApiKey').value = '';
      $('dmLlmApiKey').type = $('dmLlmShowKey').checked ? 'text' : 'password';
      $('dmLlmEnabled').checked = true;
      $('dmLlmDefault').checked = !llmServices.length;
      $('dmLlmVerifyTls').checked = true;
      $('dmLlmSave').textContent = '新增模型服务';
    }

    if ($('dmLlmShowKey')) {
      $('dmLlmShowKey').addEventListener('change', ()=>{
        $('dmLlmApiKey').type = $('dmLlmShowKey').checked ? 'text' : 'password';
      });
    }
    if ($('dmLlmReset')) $('dmLlmReset').addEventListener('click', resetLlmServiceForm);
    if ($('dmLlmSave')) {
      $('dmLlmSave').addEventListener('click', async ()=>{
        try {
          const serviceId = $('dmLlmServiceId').value.trim();
          const body = {
            name: $('dmLlmName').value.trim(),
            model: $('dmLlmModel').value.trim(),
            base_url: $('dmLlmBaseUrl').value.trim(),
            api_key: $('dmLlmApiKey').value.trim(),
            enabled: $('dmLlmEnabled').checked,
            is_default: $('dmLlmDefault').checked,
            verify_tls: $('dmLlmVerifyTls').checked,
          };
          if (!serviceId && !body.api_key) throw new Error('新增模型服务时必须填写 API Key');
          if (serviceId && !body.api_key) delete body.api_key;
          await apiFetch(
            serviceId ? '/api/admin/llm-service/' + serviceId : '/api/admin/llm-service/create',
            {method: serviceId ? 'PUT' : 'POST', body: JSON.stringify(body)}
          );
          showToast(serviceId ? '模型服务已保存' : '模型服务已创建');
          await renderAdminDatasources();
        } catch (e) { showToast(e.message, true); }
      });
    }
    document.querySelectorAll('[data-llm-edit]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const service = window._adminLlmServiceMap && window._adminLlmServiceMap[parseInt(btn.dataset.llmEdit, 10)];
        if (!service) { showToast('模型服务不存在', true); return; }
        $('dmLlmServiceId').value = String(service.id);
        $('dmLlmName').value = service.name || '';
        $('dmLlmModel').value = service.model || '';
        $('dmLlmBaseUrl').value = service.base_url || '';
        $('dmLlmApiKey').value = '';
        $('dmLlmEnabled').checked = !!service.enabled;
        $('dmLlmDefault').checked = !!service.is_default;
        $('dmLlmVerifyTls').checked = service.verify_tls !== false;
        $('dmLlmApiKey').type = $('dmLlmShowKey').checked ? 'text' : 'password';
        $('dmLlmSave').textContent = '保存模型服务';
      });
    });
    document.querySelectorAll('[data-llm-delete]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        const service = window._adminLlmServiceMap && window._adminLlmServiceMap[parseInt(btn.dataset.llmDelete, 10)];
        if (!service) { showToast('模型服务不存在', true); return; }
        if (!confirm(`确认删除模型服务“${service.name}”？`)) return;
        try {
          const resp = await apiFetch('/api/admin/llm-service/' + service.id, {method:'DELETE'});
          showToast(resp.message || '模型服务已删除');
          await renderAdminDatasources();
        } catch (e) { showToast(e.message, true); }
      });
    });

    if ($('dmAiDocFile')) {
      $('dmAiDocFile').addEventListener('change', async (e)=>{
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        try {
          $('dmAiDocText').value = await file.text();
          if (!$('dmAiFileName').value.trim()) $('dmAiFileName').value = file.name;
          $('dmAiParseResult').textContent = `已载入 ${file.name}，可以开始解析。`;
        } catch {
          showToast('读取文档失败', true);
        }
      });
    }
    if ($('dmAiClearDoc')) {
      $('dmAiClearDoc').addEventListener('click', ()=>{
        $('dmAiDocText').value = '';
        $('dmAiFileName').value = '';
        $('dmAiParseResult').textContent = llmServices.length ? '文档已清空。' : '当前还没有模型服务配置。';
        window._dmDatasourceDraftFieldLabels = {};
        window._dmDatasourceLastParse = null;
      });
    }
    if ($('dmAiParseDoc')) {
      $('dmAiParseDoc').addEventListener('click', async ()=>{
        const serviceId = $('dmAiService').value;
        const docText = $('dmAiDocText').value.trim();
        if (!serviceId) { showToast('请先选择模型服务', true); return; }
        if (!docText) { showToast('请先粘贴或上传接口文档', true); return; }
        const btn = $('dmAiParseDoc');
        const oldLabel = btn.textContent;
        btn.disabled = true;
        btn.textContent = '解析中...';
        try {
          const result = await apiFetch('/api/admin/datasource/parse-doc', {
            method:'POST',
            body: JSON.stringify({
              service_id: parseInt(serviceId, 10),
              filename: $('dmAiFileName').value.trim(),
              document_text: docText,
            }),
          });
          const requestConfig = Object.assign({}, result.request_config || {});
          if (Array.isArray(result.parameter_docs) && result.parameter_docs.length) requestConfig.parameter_docs = result.parameter_docs;
          $('dmDsKey').value = result.source_key || '';
          $('dmDsName').value = result.source_name || '';
          $('dmDsTable').value = result.table_name || '';
          $('dmDsMethod').value = result.http_method || 'POST';
          $('dmDsUrl').value = result.api_url || '';
          $('dmDsDesc').value = result.description || '';
          $('dmDsSearchable').value = Array.isArray(result.searchable_fields) ? result.searchable_fields.join(', ') : '';
          $('dmDsVerifyTls').checked = result.verify_tls !== false;
          $('dmDsQualityRules').value = prettyJson(result.quality_rules || {});
          $('dmDsReqCfg').value = prettyJson(requestConfig);
          $('dmDsRespCfg').value = prettyJson(result.response_config || {});
          window._dmDatasourceDraftFieldLabels = result.field_labels || {};
          window._dmDatasourceLastParse = result;
          const fieldCount = Object.keys(result.field_labels || {}).length;
          const paramCount = Array.isArray(result.parameter_docs) ? result.parameter_docs.length : 0;
          const warnings = Array.isArray(result.warnings) && result.warnings.length ? `；待确认：${result.warnings.join('；')}` : '';
          $('dmAiParseResult').textContent = `已回填表单：字段 ${fieldCount} 个，参数 ${paramCount} 个${warnings}`;
          showToast('接口文档已解析并回填');
        } catch (e) { showToast(e.message, true); }
        finally {
          btn.disabled = false;
          btn.textContent = oldLabel;
        }
      });
    }
    resetLlmServiceForm();

    // Platform: create
    $('dmPtCreate').addEventListener('click', async ()=>{
      const name = $('dmPtName').value.trim();
      if (!name) { showToast('请输入平台名称', true); return; }
      try {
        await apiFetch('/api/admin/platform/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description:$('dmPtDesc').value.trim()})});
        showToast('平台已创建'); await loadDynamicPlatforms(); renderAdminDatasources();
      } catch(e) { showToast(e.message, true); }
    });

    // Platform: reorder
    let _ptOrder = platforms.map(p => p.id);
    async function _savePtOrder() {
      try {
        await apiFetch('/api/admin/platform/reorder', {method:'POST', body:JSON.stringify({ids:_ptOrder})});
        await loadDynamicPlatforms();
      } catch(e) { showToast(e.message, true); }
    }
    document.querySelectorAll('.dm-pt-up').forEach(btn => {
      btn.addEventListener('click', async () => {
        const idx = parseInt(btn.dataset.ptIdx);
        if (idx <= 0) return;
        [_ptOrder[idx-1], _ptOrder[idx]] = [_ptOrder[idx], _ptOrder[idx-1]];
        await _savePtOrder();
        renderAdminDatasources();
      });
    });
    document.querySelectorAll('.dm-pt-dn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const idx = parseInt(btn.dataset.ptIdx);
        if (idx >= _ptOrder.length - 1) return;
        [_ptOrder[idx], _ptOrder[idx+1]] = [_ptOrder[idx+1], _ptOrder[idx]];
        await _savePtOrder();
        renderAdminDatasources();
      });
    });

    // Platform: edit
    document.querySelectorAll('.dm-pt-edit').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const pid = btn.dataset.ptId;
        const newName = prompt('修改平台名称：', btn.dataset.ptName);
        if (newName === null) return;
        const newDesc = prompt('修改平台描述（可选）：', btn.dataset.ptDesc);
        if (newDesc === null) return;
        apiFetch('/api/admin/platform/'+pid, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:newName.trim(),description:newDesc.trim()})})
          .then(async ()=>{ showToast('平台已更新'); await loadDynamicPlatforms(); renderAdminDatasources(); })
          .catch(e=>showToast(e.message, true));
      });
    });

    // Platform: delete
    document.querySelectorAll('.dm-pt-del').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        if (btn.disabled) {
          showToast(`该平台仍有 ${btn.dataset.ptActive||0} 个启用中的数据源，请先停用或迁移。`, true);
          return;
        }
        const inactiveCount = parseInt(btn.dataset.ptInactive||'0', 10);
        const msg = inactiveCount > 0
          ? `确认删除该平台？

系统会自动解除 ${inactiveCount} 个已停用数据源的关联，并保留配置和历史数据。`
          : '确认删除这个空平台吗？';
        if (!confirm(msg)) return;
        try {
          const r = await apiFetch('/api/admin/platform/'+btn.dataset.ptId,{method:'DELETE'});
          showToast(r.message || '平台已删除');
          await loadDynamicPlatforms();
          renderAdminDatasources();
        } catch(e) { showToast(e.message, true); }
      });
    });

    // Datasource: edit
    document.querySelectorAll('[data-ds-edit]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const dsId = parseInt(btn.dataset.dsEdit);
        window._openDsEditById(dsId);
      });
    });
    document.querySelectorAll('[data-ds-delete]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const dsId = parseInt(btn.dataset.dsDelete, 10);
        const ds = window._adminDsMap && window._adminDsMap[dsId];
        if (!ds) { showToast('数据源不存在或已刷新', true); return; }
        openDatasourceDeleteModal(ds);
      });
    });

    document.querySelectorAll('[data-ds-versions]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        window.openDsDetail(btn.dataset.dsVersions);
      });
    });

    // Datasource: create
    $('dmDsCreate').addEventListener('click', async ()=>{
      try {
        const platformVal = $('dmDsPlatform').value;
        const body = {
          source_key:$('dmDsKey').value.trim(), source_name:$('dmDsName').value.trim(),
          table_name:$('dmDsTable').value.trim(), http_method:$('dmDsMethod').value,
          api_url:$('dmDsUrl').value.trim(), token:$('dmDsToken').value.trim(),
          platform_id: platformVal ? parseInt(platformVal) : null,
          description:$('dmDsDesc').value.trim(),
          searchable_fields: parseCommaList($('dmDsSearchable').value),
          quality_rules: parseJsonInput($('dmDsQualityRules').value, '质量规则'),
          verify_tls: $('dmDsVerifyTls').checked,
          field_labels: window._dmDatasourceDraftFieldLabels || {},
          request_config: parseJsonInput($('dmDsReqCfg').value, '请求配置'),
          response_config: parseJsonInput($('dmDsRespCfg').value, '响应配置'),
        };
        await apiFetch('/api/admin/datasource/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        window._dmDatasourceDraftFieldLabels = {};
        window._dmDatasourceLastParse = null;
        showToast('数据源已创建'); await loadDynamicPlatforms(); renderAdminDatasources();
      } catch(e) { showToast(e.message, true); }
    });

    // Datasource: test connection
    $('dmDsTest').addEventListener('click', async ()=>{
      try {
        const body = {
          source_key:$('dmDsKey').value.trim()||'__test__', source_name:$('dmDsName').value.trim()||'test',
          table_name:$('dmDsTable').value.trim()||'__test__', http_method:$('dmDsMethod').value,
          api_url:$('dmDsUrl').value.trim(), token:$('dmDsToken').value.trim(),
          description:$('dmDsDesc').value.trim(),
          searchable_fields: parseCommaList($('dmDsSearchable').value),
          quality_rules: parseJsonInput($('dmDsQualityRules').value, '质量规则'),
          verify_tls: $('dmDsVerifyTls').checked,
          request_config: parseJsonInput($('dmDsReqCfg').value, '请求配置'),
          response_config: parseJsonInput($('dmDsRespCfg').value, '响应配置'),
        };
        const r = await apiFetch('/api/admin/datasource/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        showToast(r.status_code===200?`连接成功，返回 ${r.row_count} 条数据`:`HTTP ${r.status_code}: ${r.message||''}`);
      } catch(e) { showToast(e.message, true); }
    });

    // Datasource: disable
    document.querySelectorAll('[data-ds-disable]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        if (!confirm('确认停用该数据源？\n\n停用后会从导航与自动同步中移除，但会保留配置和历史数据。')) return;
        try {
          const r = await apiFetch('/api/admin/datasource/'+btn.dataset.dsDisable+'/status', {method:'PUT', body: JSON.stringify({enabled:0})});
          showToast(r.message || '数据源已停用');
          await loadDynamicPlatforms();
          renderAdminDatasources();
        } catch(e) { showToast(e.message, true); }
      });
    });

    // Datasource: enable
    document.querySelectorAll('[data-ds-enable]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        try {
          const r = await apiFetch('/api/admin/datasource/'+btn.dataset.dsEnable+'/status', {method:'PUT', body: JSON.stringify({enabled:1})});
          showToast(r.message || '数据源已启用');
          await loadDynamicPlatforms();
          renderAdminDatasources();
        } catch(e) { showToast(e.message, true); }
      });
    });
  }
  let _syncCountdownTimer = null;

  function fmtSecs(s) {
    if (s == null || s < 0) return '—';
    if (s < 60) return `${s} 秒`;
    const m = Math.floor(s / 60), sec = s % 60;
    return sec ? `${m} 分 ${sec} 秒` : `${m} 分钟`;
  }

  async function renderAdminSync() {
    const logs = state.admin.syncLogs;
    // 先渲染骨架
    $('mainContent').innerHTML = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">数据同步</h1>
      <p class="text-sm text-slate-500 mt-1">自动定时同步 · 手动触发 · 冷却保护</p></div>

    <!-- 自动同步状态卡 -->
    <div class="admin-panel" style="margin-bottom:16px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
        <h3 style="margin:0;">自动同步</h3>
        <span id="dmAutoSyncBadge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:#dcfce7;color:#16a34a;">加载中…</span>
        <div style="margin-left:auto;display:flex;gap:8px;align-items:center;">
          <span style="font-size:12px;color:#64748b;">间隔</span>
          <div id="dmSyncIntervalHms">${hmsInputsHtml('dmSyncInterval', 3600)}</div>
          <button class="dm-btn" id="dmSyncIntervalSave" style="font-size:12px;padding:4px 12px;">保存</button>
          <button class="dm-btn" id="dmAutoSyncToggle" style="font-size:12px;padding:4px 12px;">-</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;" id="dmAutoSyncInfo">
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;">
          <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;">下次同步</div>
          <div id="dmNextSyncIn" style="font-size:18px;font-weight:700;color:#334155;">—</div>
        </div>
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;">
          <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;">当前状态</div>
          <div id="dmSyncRunning" style="font-size:14px;font-weight:600;color:#64748b;">闲置</div>
        </div>
      </div>
    </div>

    <!-- 数据源冷却状态 -->
    <div class="admin-panel" style="margin-bottom:16px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
        <h3 style="margin:0;">手动同步</h3>
        <span style="font-size:12px;color:#94a3b8;" id="dmCooldownHint">冷却期内数据源将自动跳过</span>
        <button class="dm-btn primary" id="dmSyncTrigger" style="margin-left:auto;">触发全量同步</button>
      </div>
      <div id="dmDsStatusList" style="display:flex;flex-direction:column;gap:6px;">
        <div style="color:#94a3b8;font-size:13px;">加载中…</div>
      </div>
    </div>

    <!-- 同步日志 -->
    <div class="admin-panel">
      <h3>同步日志（最近 ${logs.length} 条）</h3>
      <div style="overflow:auto;"><table class="dm-table">
        <thead><tr><th>时间</th><th>数据源</th><th>版本</th><th>行数</th><th>耗时</th><th>同步状态</th><th>质量状态</th><th>信息</th></tr></thead>
        <tbody>${logs.map(l=>{
          const icon = formatStatusText(l.status);
          let qualitySummary = '';
          try { qualitySummary = JSON.parse(l.quality_report || '{}').summary || ''; } catch(e) {}
          return `<tr>
            <td style="white-space:nowrap;">${l.started_at||''}</td>
            <td>${escapeHtml(l.source_name)}</td>
            <td style="font-size:11px;white-space:normal;max-width:180px;color:#475569;">${escapeHtml(l.sync_version || '-')}</td>
            <td>${l.row_count?.toLocaleString()||0}</td>
            <td>${l.duration_ms}ms</td>
            <td>${icon}</td>
            <td style="font-size:11px;white-space:normal;max-width:220px;">${escapeHtml(formatStatusText(l.quality_status || 'na'))}<div style="color:#94a3b8;">${escapeHtml(qualitySummary)}</div></td>
            <td style="max-width:320px;white-space:normal;font-size:11px;color:#64748b;">${escapeHtml(l.message||'')}</td>
          </tr>`;
        }).join('')}</tbody>
      </table></div>
    </div>`;

    // 加载状态并驱动 UI
    let syncStatus = null;
    try { syncStatus = await apiFetch('/api/admin/sync/status'); } catch(e) {}

    function applyStatus(st) {
      if (!st) return;
      const badge = $('dmAutoSyncBadge');
      const toggle = $('dmAutoSyncToggle');
      if (badge) {
        badge.textContent = st.auto_enabled ? '● 运行中' : '● 已暂停';
        badge.style.background = st.auto_enabled ? '#dcfce7' : '#f1f5f9';
        badge.style.color = st.auto_enabled ? '#16a34a' : '#64748b';
      }
      if (toggle) { toggle.textContent = st.auto_enabled ? '暂停' : '启用'; }
      const intervalSec = st.interval_seconds || 3600;
      const hmsWrap = $('dmSyncIntervalHms');
      if (hmsWrap) { hmsWrap.innerHTML = hmsInputsHtml('dmSyncInterval', intervalSec); }
      if ($('dmSyncRunning')) {
        $('dmSyncRunning').textContent = st.is_syncing ? '⏳ 同步中…' : '闲置';
        $('dmSyncRunning').style.color = st.is_syncing ? '#d97706' : '#64748b';
      }
      // 数据源冷却 + 各自同步间隔列表
      const list = $('dmDsStatusList');
      if (list && st.cooldowns) {
        const entries = Object.values(st.cooldowns);
        const globalSec = st.interval_seconds || 3600;
        list.innerHTML = entries.length ? entries.map(d => {
          const inCooldown = d.remaining > 0;
          const lastIcon = d.last_status==='success'?'✅':d.last_status==='empty'?'∅':d.last_status==='warning'?'⚠️':d.last_status==='failed'?'❌':'—';
          const nextInSecs = d.next_sync_in;
          const nextLabel = nextInSecs != null ? fmtHMS(nextInSecs) : '—';
          const curInterval = d.sync_interval_seconds || '';
          return `<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;flex-wrap:wrap;">
            <span style="font-size:13px;font-weight:500;color:#334155;min-width:100px;">${escapeHtml(d.source_name)}</span>
            <span style="font-size:11px;color:#94a3b8;flex:1;min-width:120px;">${d.last_sync_at ? '上次: '+d.last_sync_at : '从未同步'} ${lastIcon}</span>
            <span style="font-size:11px;color:#64748b;white-space:nowrap;">下次: <span class="ds-next-in" data-secs="${nextInSecs??''}">${nextLabel}</span></span>
            <label style="display:flex;align-items:center;gap:4px;font-size:11px;color:#64748b;white-space:nowrap;">
              间隔
              <div class="ds-interval-hms" data-sk="${d.source_key}" data-cur="${curInterval}">${hmsInputsHtml('dsInterval_'+d.source_key, curInterval || globalSec, {small:true})}</div>
              <button class="dm-tbl-btn ds-interval-save" data-sk="${d.source_key}" style="font-size:11px;padding:2px 8px;">保存</button>
              ${curInterval ? `<button class="dm-tbl-btn ds-interval-reset" data-sk="${d.source_key}" style="font-size:11px;padding:2px 8px;">恢复全局</button>` : ''}
            </label>
            ${inCooldown
              ? `<span class="dmCdBadge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:#fef9c3;color:#92400e;white-space:nowrap;">冷却 <span class="cd-sec">${d.remaining}</span>s</span>`
              : `<span style="font-size:11px;padding:2px 8px;border-radius:10px;background:#dcfce7;color:#16a34a;">就绪</span>`}
            <button class="dm-tbl-btn" data-sync-key="${d.source_key}" data-sync-name="${escapeHtml(d.source_name)}" style="font-size:11px;padding:3px 10px;white-space:nowrap;">同步</button>
          </div>`;
        }).join('') : '<div style="color:#94a3b8;font-size:13px;">暂无数据源</div>';
        // 绑定单独同步按钮
        list.querySelectorAll('[data-sync-key]').forEach(btn => {
          btn.addEventListener('click', () => doSync(btn.dataset.syncKey, btn.dataset.syncName));
        });
        // 绑定保存间隔按钮
        list.querySelectorAll('.ds-interval-save').forEach(btn => {
          btn.addEventListener('click', async () => {
            const sk = btn.dataset.sk;
            const h = $(`dsInterval_${sk}H`).value;
            const m = $(`dsInterval_${sk}M`).value;
            const s = $(`dsInterval_${sk}S`).value;
            const total = parseHMS(h, m, s);
            if (total < 10) { showToast('同步间隔至少 10 秒', true); return; }
            try {
              await apiFetch(`/api/admin/datasource/${sk}/sync-interval`, {
                method: 'PUT', body: JSON.stringify({interval_seconds: total})
              });
              showToast(`${sk} 同步间隔设为 ${fmtHMS(total)}`);
            } catch(e) { showToast(e.message, true); }
          });
        });
        // 绑定恢复全局按钮
        list.querySelectorAll('.ds-interval-reset').forEach(btn => {
          btn.addEventListener('click', async () => {
            const sk = btn.dataset.sk;
            try {
              await apiFetch(`/api/admin/datasource/${sk}/sync-interval`, {
                method: 'PUT', body: JSON.stringify({interval_seconds: null})
              });
              showToast(`${sk} 已恢复全局间隔`);
            } catch(e) { showToast(e.message, true); }
          });
        });
      }
    }
    applyStatus(syncStatus);

    // 倒计时 & 冷却更新（每秒）
    if (_syncCountdownTimer) clearInterval(_syncCountdownTimer);
    let secLeft = syncStatus?.seconds_until_next ?? null;
    const cdState = {};  // source_key -> remaining secs
    if (syncStatus?.cooldowns) {
      Object.entries(syncStatus.cooldowns).forEach(([k,v]) => { cdState[k] = v.remaining; });
    }
    _syncCountdownTimer = setInterval(() => {
      // 全局下次同步倒计时
      if (secLeft !== null && secLeft > 0) {
        secLeft--;
        if ($('dmNextSyncIn')) $('dmNextSyncIn').textContent = fmtHMS(secLeft);
      } else if (secLeft === 0) {
        if ($('dmNextSyncIn')) $('dmNextSyncIn').textContent = '即将同步…';
      }
      // 冷却倒计时
      document.querySelectorAll('.dmCdBadge').forEach(badge => {
        const span = badge.querySelector('.cd-sec');
        if (!span) return;
        let s = parseInt(span.textContent) - 1;
        if (s <= 0) {
          badge.style.background = '#dcfce7'; badge.style.color = '#16a34a';
          badge.innerHTML = '就绪'; badge.classList.remove('dmCdBadge');
        } else { span.textContent = s; }
      });
      // 各数据源下次同步倒计时
      document.querySelectorAll('.ds-next-in').forEach(el => {
        let s = parseInt(el.dataset.secs);
        if (isNaN(s)) return;
        s = Math.max(0, s - 1);
        el.dataset.secs = s;
        el.textContent = s > 0 ? fmtHMS(s) : '即将同步…';
      });
    }, 1000);
    if (secLeft !== null && $('dmNextSyncIn')) $('dmNextSyncIn').textContent = fmtHMS(secLeft);

    // 保存全局间隔
    $('dmSyncIntervalSave').addEventListener('click', async ()=>{
      const total = parseHMS($('dmSyncIntervalH').value, $('dmSyncIntervalM').value, $('dmSyncIntervalS').value);
      if (total < 10) { showToast('同步间隔至少 10 秒', true); return; }
      try {
        await apiFetch('/api/admin/sync/config', {method:'PUT', body: JSON.stringify({interval_seconds: total})});
        showToast(`同步间隔已设为 ${fmtHMS(total)}`);
        secLeft = total;
      } catch(e) { showToast(e.message, true); }
    });

    // 暂停/启用自动同步
    $('dmAutoSyncToggle').addEventListener('click', async ()=>{
      const cur = $('dmAutoSyncBadge').textContent.includes('运行');
      try {
        const r = await apiFetch('/api/admin/sync/config', {method:'PUT', body: JSON.stringify({auto_enabled: !cur})});
        applyStatus({...syncStatus, auto_enabled: r.auto_enabled, interval_seconds: r.interval_seconds});
        showToast(r.auto_enabled ? '自动同步已启用' : '自动同步已暂停');
        if (r.auto_enabled) { secLeft = r.interval_seconds || 3600; }
      } catch(e) { showToast(e.message, true); }
    });

    // 渲染进度条 HTML
    function _renderProgressItems(items, isCompleted) {
      const statusIcon = s => s==='success'?'✅':s==='empty'?'∅':s==='warning'?'⚠️':s==='failed'?'❌':s==='syncing'?'⏳':(s==='pending'||s==='queued')?'⏸':'⏸';
      const statusColor = s => s==='success'?'#16a34a':s==='empty'?'#0f766e':s==='warning'?'#d97706':s==='failed'?'#dc2626':'#64748b';
      const statusBg = s => s==='success'?'#f0fdf4':s==='empty'?'#f0fdfa':s==='warning'?'#fffbeb':s==='failed'?'#fff1f2':'#f8fafc';
      const statusBdr = s => s==='success'?'#bbf7d0':s==='empty'?'#99f6e4':s==='warning'?'#fde68a':s==='failed'?'#fecaca':'#e2e8f0';
      return items.map(item => {
        const pct = (item.total && item.total > 0) ? Math.min(100, Math.round(item.fetched / item.total * 100)) : (['success','empty','warning'].includes(item.status) ? 100 : 0);
        const showBar = item.status === 'syncing' || (isCompleted && (['success','empty','warning'].includes(item.status)));
        const fetched = item.fetched || item.row_count || 0;
        const total = item.total;
        const syncVersion = item.sync_version || '';
        return `
        <div style="border:1px solid ${statusBdr(item.status)};border-radius:10px;padding:14px 16px;background:${statusBg(item.status)};">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:${showBar?'10px':'0'};">
            <span style="font-size:16px;">${statusIcon(item.status)}</span>
            <span style="font-weight:600;color:#1e293b;font-size:14px;">${escapeHtml(item.source_name)}</span>
            <span style="margin-left:auto;font-size:12px;color:${statusColor(item.status)};font-weight:500;white-space:nowrap;">
              ${fetched.toLocaleString()}${total ? ' / ' + total.toLocaleString() + ' 条' : ' 条'}
            </span>
          </div>
          ${syncVersion ? `<div style="font-size:11px;color:#64748b;margin-bottom:${showBar?'8px':'6px'};">版本：<span style="color:#0f172a;font-weight:600;">${escapeHtml(syncVersion)}</span></div>` : ''}
          ${showBar ? `
          <div style="background:#e2e8f0;border-radius:6px;height:8px;overflow:hidden;">
            <div style="height:100%;border-radius:6px;background:${item.status==='warning'?'#f59e0b':item.status==='failed'?'#ef4444':item.status==='empty'?'#14b8a6':'#3b82f6'};width:${pct}%;transition:width .4s ease;"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:5px;">
            <span style="font-size:11px;color:#94a3b8;">${item.message ? escapeHtml(item.message.slice(0,60)) : (item.status==='syncing' ? '正在拉取...' : '')}</span>
            <span style="font-size:11px;color:#64748b;font-weight:600;">${pct}%</span>
          </div>` : ((item.status==='pending'||item.status==='queued')?`<div style="font-size:12px;color:#94a3b8;margin-top:6px;">等待中...</div>`:'')}
        </div>`;
      }).join('');
    }

    // 通用同步执行函数（sourceKey=null 表示全量）
    let _syncPollTimer = null;
    async function doSync(sourceKey, sourceName) {
      const isAll = !sourceKey;
      $('dmSyncProgressTitle').textContent = isAll ? '全量同步' : `同步：${sourceName||sourceKey}`;
      $('dmSyncProgressBody').innerHTML = `<div style="text-align:center;padding:40px 20px;"><div class="dm-spinner" style="margin:0 auto 16px;"></div><p style="font-size:14px;color:#64748b;">正在启动同步...</p></div>`;
      $('dmSyncProgressMask').classList.remove('dm-hidden');

      // 触发同步（立即返回，后台执行）
      try {
        const body = sourceKey ? JSON.stringify({source_key: sourceKey}) : '{}';
        await apiFetch('/api/admin/sync/trigger', {method:'POST', body});
      } catch(e) {
        $('dmSyncProgressTitle').textContent = e.message.includes('冷却') ? '触发被限制' : '触发失败';
        $('dmSyncProgressBody').innerHTML = `<div style="text-align:center;padding:32px;"><div style="font-size:40px;margin-bottom:12px;">${e.message.includes('冷却')?'⏳':'❌'}</div><p style="color:#dc2626;font-size:14px;">${escapeHtml(e.message)}</p></div>`;
        return;
      }

      // 显示停止按钮
      const stopBtn = $('dmSyncStopBtn');
      stopBtn.classList.remove('dm-hidden');
      stopBtn.textContent = '停止同步';
      stopBtn.disabled = false;
      stopBtn.onclick = async () => {
        stopBtn.textContent = '停止中...';
        stopBtn.disabled = true;
        try {
          await apiFetch('/api/admin/sync/cancel', {method:'POST', body:'{}'});
        } catch(e) {
          showToast(e.message, true);
          stopBtn.textContent = '停止同步';
          stopBtn.disabled = false;
        }
      };

      // 轮询进度
      if (_syncPollTimer) clearInterval(_syncPollTimer);
      _syncPollTimer = setInterval(async () => {
        try {
          const prog = await apiFetch('/api/admin/sync/progress');
          const items = prog.items || [];

          if (!prog.is_active) {
            clearInterval(_syncPollTimer); _syncPollTimer = null;
            stopBtn.classList.add('dm-hidden');
            const result = prog.result || {};
            const summary = result.summary || {};
            const isAll2 = !sourceKey;
            const isCancelled = (result.error || '').includes('停止');
            $('dmSyncProgressTitle').textContent = isCancelled ? '同步已停止' : (isAll2 ? '同步完成' : `完成：${sourceName||sourceKey}`);
            $('dmSyncProgressBody').innerHTML = `
              ${result.error ? `<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:14px 16px;margin-bottom:14px;color:#c2410c;font-size:13px;">⚠️ ${escapeHtml(result.error)}</div>` : ''}
              ${isAll2 && summary.total ? `<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px;">
                ${[['成功','success','#16a34a'],['空结果','empty','#0f766e'],['降级','warning','#d97706'],['失败','failed','#dc2626'],['总计','total','#334155']].map(([label,key,color])=>`
                <div style="text-align:center;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 6px;">
                  <div style="font-size:26px;font-weight:700;color:${color};">${summary[key]??0}</div>
                  <div style="font-size:11px;color:#64748b;margin-top:3px;">${label}</div>
                </div>`).join('')}
              </div>` : ''}
              <div style="display:flex;flex-direction:column;gap:10px;">
                ${_renderProgressItems(
                  (result.items || items).map(it => ({...it, message: it.message||''})),
                  true
                )}
              </div>`;
            await loadAdminData();
            if (document.getElementById('dmDsStatusList')) await renderAdminSync();
            await loadDynamicPlatforms();
            return;
          }

          // 进行中：更新进度条
          $('dmSyncProgressTitle').textContent = isAll ? `全量同步中…` : `同步中：${sourceName||sourceKey}`;
          $('dmSyncProgressBody').innerHTML = `<div style="display:flex;flex-direction:column;gap:10px;">${_renderProgressItems(items, false)}</div>`;
        } catch(e) { /* 网络短暂抖动，继续轮询 */ }
      }, 800);
    }

    // 手动触发全量同步
  $('dmSyncTrigger').addEventListener('click', () => doSync(null));
  $('dmSyncProgressClose').addEventListener('click', () => $('dmSyncProgressMask').classList.add('dm-hidden'));
  $('dmSyncProgressMask').addEventListener('click', e=>{ if(e.target===$('dmSyncProgressMask')) $('dmSyncProgressMask').classList.add('dm-hidden'); });
  }

  async function renderAdminMcp() {
    const keyword = state.admin.mcpKeyword || '';
    const status = state.admin.mcpStatus || 'active';
    let data = {items:[], stats:{all:0,active:0,revoked:0,expired:0}};
    try {
      data = await apiFetch('/api/admin/mcp-token/list?status=' + encodeURIComponent(status) + '&keyword=' + encodeURIComponent(keyword) + '&limit=100', {method:'POST'});
    } catch (e) {
      $('mainContent').innerHTML = `<div class="admin-panel"><p style="color:#dc2626;">加载 MCP 看板失败: ${escapeHtml(e.message)}</p></div>`;
      return;
    }
    const items = data.items || [];
    const stats = data.stats || {all:0,active:0,revoked:0,expired:0};
    function statusBadge(value) {
      if (value === 'active') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#dcfce7;color:#166534;">有效中</span>';
      if (value === 'revoked') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fee2e2;color:#b91c1c;">已停用</span>';
      return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#e2e8f0;color:#475569;">已过期</span>';
    }
    $('mainContent').innerHTML = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">MCP 看板</h1><p class="text-sm text-slate-500 mt-1">查看用户导出的 MCP 令牌，管理员可手动停用。导出 IP 与最近使用 IP 仅作审计，不参与鉴权。</p></div>
    <div class="admin-panel">
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;" class="dm-metrics">
        ${renderMetricCard(stats.all||0, '总导出数')}
        ${renderMetricCard(stats.active||0, '有效令牌')}
        ${renderMetricCard(stats.revoked||0, '已停用')}
        ${renderMetricCard(stats.expired||0, '已过期')}
      </div>
    </div>
    <div class="admin-panel">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;">
        <input class="dm-search" id="dmMcpKeyword" placeholder="按用户名、工号、部门、数据源或 IP 搜索" value="${escapeHtml(keyword)}" style="flex:1;min-width:260px;max-width:420px;">
        <select class="dm-input" id="dmMcpStatus" style="max-width:160px;">
          <option value="active" ${status==='active'?'selected':''}>仅看有效中</option>
          <option value="revoked" ${status==='revoked'?'selected':''}>仅看已停用</option>
          <option value="expired" ${status==='expired'?'selected':''}>仅看已过期</option>
          <option value="all" ${status==='all'?'selected':''}>全部状态</option>
        </select>
        <button class="dm-btn primary" id="dmMcpRefresh">刷新</button>
      </div>
      <div style="overflow:auto;">
        <table class="dm-table">
          <thead><tr><th>ID</th><th>用户</th><th>数据源</th><th>导出IP</th><th>创建 / 到期</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${items.length ? items.map(item=>`
            <tr>
              <td>${item.id}</td>
              <td style="white-space:normal;min-width:160px;">
                <div style="font-weight:600;color:#0f172a;">${escapeHtml(item.username || '')}</div>
                <div style="font-size:11px;color:#64748b;">工号: ${escapeHtml(item.employee_no || '-')} · 部门: ${escapeHtml(item.department || '-')}</div>
                <div style="font-size:11px;color:#94a3b8;">JTI: ${escapeHtml(item.jti_short || '')}</div>
              </td>
              <td style="white-space:normal;min-width:180px;">${escapeHtml((item.source_keys || []).join(', ') || '-')}</td>
              <td style="white-space:normal;min-width:150px;">
                <div style="font-size:11px;color:#0f172a;">${escapeHtml(item.ip || '-')}</div>
                <div style="font-size:11px;color:#94a3b8;">${item.bind_ip ? '旧令牌曾启用绑定标记' : '仅审计，不限IP'}</div>
              </td>
              <td style="white-space:normal;min-width:170px;">
                <div style="font-size:11px;color:#0f172a;">创建: ${escapeHtml(item.created_at || '-')}</div>
                <div style="font-size:11px;color:#64748b;">到期: ${escapeHtml(item.expires_at || '-')}</div>
              </td>
              <td style="white-space:normal;min-width:170px;">
                <div style="font-size:11px;color:#0f172a;">${escapeHtml(item.last_used_at || '从未使用')}</div>
                <div style="font-size:11px;color:#64748b;">${escapeHtml(item.last_used_ip || '')}</div>
              </td>
              <td style="white-space:normal;min-width:150px;">
                ${statusBadge(item.status)}
                ${item.revoked_by ? `<div style="font-size:11px;color:#64748b;margin-top:5px;">停用人: ${escapeHtml(item.revoked_by)}</div>` : ''}
                ${item.revoked_reason ? `<div style="font-size:11px;color:#94a3b8;margin-top:4px;">原因: ${escapeHtml(item.revoked_reason)}</div>` : ''}
              </td>
              <td>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                  <button class="dm-tbl-btn" data-mcp-view="${item.id}">查看原文</button>
                  ${item.status === 'active'
                    ? `<button class="dm-tbl-btn danger" data-mcp-revoke="${item.id}" data-mcp-user="${escapeHtml(item.username || '')}">停用</button>`
                    : '<span style="font-size:11px;color:#94a3b8;">-</span>'}
                </div>
              </td>
            </tr>`).join('') : '<tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:20px;">暂无可展示的 MCP 令牌记录</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;

    $('dmMcpRefresh').addEventListener('click', ()=>{
      state.admin.mcpKeyword = $('dmMcpKeyword').value.trim();
      state.admin.mcpStatus = $('dmMcpStatus').value;
      renderAdminMcp();
    });
    $('dmMcpKeyword').addEventListener('keydown', (e)=>{
      if (e.key === 'Enter') {
        state.admin.mcpKeyword = $('dmMcpKeyword').value.trim();
        state.admin.mcpStatus = $('dmMcpStatus').value;
        renderAdminMcp();
      }
    });
    $('dmMcpStatus').addEventListener('change', ()=>{
      state.admin.mcpKeyword = $('dmMcpKeyword').value.trim();
      state.admin.mcpStatus = $('dmMcpStatus').value;
      renderAdminMcp();
    });
    document.querySelectorAll('[data-mcp-view]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const tokenId = parseInt(btn.dataset.mcpView, 10);
        viewMcpOriginal(tokenId, true);
      });
    });
    document.querySelectorAll('[data-mcp-revoke]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        const tokenId = parseInt(btn.dataset.mcpRevoke, 10);
        const username = btn.dataset.mcpUser || '';
        const reason = prompt(`确认停用用户 ${username} 的这个 MCP 令牌？\n\n可填写停用原因（可留空）：`, '');
        if (reason === null) return;
        try {
          const resp = await apiFetch('/api/admin/mcp-token/' + tokenId + '/revoke', {
            method:'POST',
            body: JSON.stringify({reason: reason.trim()}),
          });
          showToast(resp.message || 'MCP 令牌已停用');
          await renderAdminMcp();
        } catch (e) { showToast(e.message, true); }
      });
    });
  }

  // ======== 我的消息（普通用户查看自己的 MCP 申请审批结果）========
  function _myMcpStatusBadge(value) {
    if (value === 'active') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#dcfce7;color:#166534;">有效中</span>';
    if (value === 'revoked') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fee2e2;color:#b91c1c;">已停用</span>';
    return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#e2e8f0;color:#475569;">已过期</span>';
  }
  async function renderMyMcpTokens() {
    let tokenData = {items:[], stats:{all:0,active:0,revoked:0,expired:0}};
    let requestData = {items:[], unseen_count:0};
    try {
      [tokenData, requestData] = await Promise.all([
        apiFetch('/api/mcp/token/my-list', {method:'POST'}),
        apiFetch('/api/mcp/export-request/my-list', {method:'POST'}),
      ]);
    } catch (e) {
      $('mainContent').innerHTML = `<div class="admin-panel"><p style="color:#dc2626;">加载我的 MCP 列表失败: ${escapeHtml(e.message)}</p></div>`;
      return;
    }
    const items = tokenData.items || [];
    const stats = tokenData.stats || {all:0,active:0,revoked:0,expired:0};
    const requests = (requestData.items || []).filter(item => item.status === 'pending');
    $('mainContent').innerHTML = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">我的 MCP 列表</h1><p class="text-sm text-slate-500 mt-1">查看已导出的 MCP 令牌、原文配置以及正在申请中的导出权限。</p></div>
    <div class="admin-panel">
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;" class="dm-metrics">
        ${renderMetricCard(stats.all||0, '总导出数')}
        ${renderMetricCard(stats.active||0, '有效中')}
        ${renderMetricCard(stats.revoked||0, '已停用')}
        ${renderMetricCard(stats.expired||0, '已过期')}
      </div>
    </div>
    ${requests.length ? `
    <div class="admin-panel">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
        <span style="font-size:14px;font-weight:600;color:#1e293b;">申请中</span>
        <span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fef9c3;color:#854d0e;">${requests.length} 条</span>
      </div>
      <div style="overflow:auto;">
        <table class="dm-table">
          <thead><tr><th>数据源</th><th>申请原因</th><th>申请时间</th><th>状态</th></tr></thead>
          <tbody>${requests.map(item => `
            <tr>
              <td style="white-space:normal;min-width:150px;">${escapeHtml(item.source_name || item.source_key || '-')}</td>
              <td style="white-space:normal;min-width:220px;font-size:12px;color:#334155;">${escapeHtml(item.reason || '-')}</td>
              <td style="white-space:normal;min-width:140px;font-size:11px;color:#64748b;">${escapeHtml(item.created_at || '-')}</td>
              <td><span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fef9c3;color:#854d0e;">申请中</span></td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>` : ''}
    <div class="admin-panel">
      <div style="overflow:auto;">
        <table class="dm-table">
          <thead><tr><th>ID</th><th>数据源</th><th>创建 / 到期</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${items.length ? items.map(item => `
            <tr>
              <td style="white-space:normal;min-width:120px;">
                <div style="font-weight:600;color:#0f172a;">${item.id}</div>
                <div style="font-size:11px;color:#94a3b8;">JTI: ${escapeHtml(item.jti_short || '')}</div>
              </td>
              <td style="white-space:normal;min-width:220px;">
                <div style="color:#0f172a;font-size:12px;">${escapeHtml((item.source_names || item.source_keys || []).join(', ') || '-')}</div>
                <div style="font-size:11px;color:#94a3b8;margin-top:4px;">${escapeHtml((item.source_keys || []).join(', ') || '-')}</div>
              </td>
              <td style="white-space:normal;min-width:180px;">
                <div style="font-size:11px;color:#0f172a;">创建: ${escapeHtml(item.created_at || '-')}</div>
                <div style="font-size:11px;color:#64748b;">到期: ${escapeHtml(item.expires_at || '-')}</div>
              </td>
              <td style="white-space:normal;min-width:180px;">
                <div style="font-size:11px;color:#0f172a;">${escapeHtml(item.last_used_at || '从未使用')}</div>
                <div style="font-size:11px;color:#64748b;">${escapeHtml(item.last_used_ip || '')}</div>
              </td>
              <td style="white-space:normal;min-width:150px;">
                ${_myMcpStatusBadge(item.status)}
                ${item.revoked_reason ? `<div style="font-size:11px;color:#94a3b8;margin-top:4px;">${escapeHtml(item.revoked_reason)}</div>` : ''}
              </td>
              <td style="white-space:normal;min-width:150px;">
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                  ${item.status === 'active' ? `<button class="dm-tbl-btn danger" data-my-mcp-revoke="${item.id}">停用</button>` : ''}
                  <button class="dm-tbl-btn" data-my-mcp-view="${item.id}">查看原文</button>
                  <button class="dm-tbl-btn" data-my-mcp-delete="${item.id}">删除</button>
                </div>
              </td>
            </tr>`).join('') : '<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:20px;">暂无已导出的 MCP 令牌</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;

    document.querySelectorAll('[data-my-mcp-revoke]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const tokenId = parseInt(btn.dataset.myMcpRevoke, 10);
        const reason = prompt('确认停用这个 MCP 令牌？\n\n可填写停用原因（可留空）：', '');
        if (reason === null) return;
        try {
          const resp = await apiFetch('/api/mcp/token/' + tokenId + '/revoke', {
            method:'POST',
            body: JSON.stringify({reason: reason.trim()}),
          });
          showToast(resp.message || 'MCP 令牌已停用');
          await renderMyMcpTokens();
        } catch (e) { showToast(e.message, true); }
      });
    });
    document.querySelectorAll('[data-my-mcp-view]').forEach(btn => {
      btn.addEventListener('click', () => {
        const tokenId = parseInt(btn.dataset.myMcpView, 10);
        viewMcpOriginal(tokenId, false);
      });
    });
    document.querySelectorAll('[data-my-mcp-delete]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const tokenId = parseInt(btn.dataset.myMcpDelete, 10);
        const reason = prompt('确认删除这个 MCP 令牌记录？\n\n这是逻辑删除；如果令牌仍有效，会先自动停用。', '');
        if (reason === null) return;
        try {
          const resp = await apiFetch('/api/mcp/token/' + tokenId + '/delete', {
            method:'POST',
            body: JSON.stringify({reason: reason.trim()}),
          });
          showToast(resp.message || 'MCP 令牌已删除');
          await renderMyMcpTokens();
        } catch (e) { showToast(e.message, true); }
      });
    });
  }

  function _mcpReqStatusBadge(value) {
    if (value === 'pending') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fef9c3;color:#854d0e;">待处理</span>';
    if (value === 'approved') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#dcfce7;color:#166534;">已批准</span>';
    return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fee2e2;color:#b91c1c;">已驳回</span>';
  }
  async function renderMyMessages() {
    let data = {items:[], unseen_count:0};
    try {
      data = await apiFetch('/api/mcp/export-request/my-list', {method:'POST'});
    } catch (e) {
      $('mainContent').innerHTML = `<div class="admin-panel"><p style="color:#dc2626;">加载消息失败: ${escapeHtml(e.message)}</p></div>`;
      return;
    }
    const items = data.items || [];
    $('mainContent').innerHTML = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">我的消息</h1><p class="text-sm text-slate-500 mt-1">你提交的 MCP 导出权限申请及管理员审批结果</p></div>
    <div class="admin-panel">
      <div style="overflow:auto;">
        <table class="dm-table">
          <thead><tr><th>数据源</th><th>申请原因</th><th>申请时间</th><th>状态</th><th>处理详情</th></tr></thead>
          <tbody>${items.length ? items.map(item => `
            <tr>
              <td style="white-space:normal;min-width:150px;">${escapeHtml(item.source_name || item.source_key || '-')}</td>
              <td style="white-space:normal;min-width:200px;font-size:12px;color:#334155;">${escapeHtml(item.reason || '-')}</td>
              <td style="white-space:normal;min-width:140px;font-size:11px;color:#64748b;">${escapeHtml(item.created_at || '-')}</td>
              <td>${_mcpReqStatusBadge(item.status)}</td>
              <td style="white-space:normal;min-width:200px;font-size:11px;color:#64748b;">
                ${item.status === 'pending'
                  ? '等待管理员审批'
                  : `${item.handled_at ? `处理时间: ${escapeHtml(item.handled_at)}<br>` : ''}${item.admin_comment ? `备注: ${escapeHtml(item.admin_comment)}` : (item.status === 'approved' ? '已获得该数据源导出权限' : '')}`}
              </td>
            </tr>`).join('') : '<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:20px;">暂无 MCP 导出申请记录</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;
    if (data.unseen_count > 0) {
      try { await apiFetch('/api/mcp/export-request/mark-seen', {method:'POST'}); } catch(e) {}
      _userMsgLastUnseen = 0;
      const badge = $('dmUserMsgBadge');
      if (badge) badge.classList.remove('show');
    }
  }

  let _userMsgPollTimer = null;
  let _userMsgLastUnseen = null;
  async function refreshUserMsgBadge() {
    if (!state.user) return;
    try {
      const r = await apiFetch('/api/mcp/export-request/my-list', {method:'POST'});
      const unseen = r.unseen_count || 0;
      const badge = $('dmUserMsgBadge');
      if (badge) {
        badge.textContent = unseen > 99 ? '99+' : String(unseen);
        badge.classList.toggle('show', unseen > 0);
      }
      if (_userMsgLastUnseen !== null && unseen > _userMsgLastUnseen) {
        showToast('你的 MCP 导出申请有新的审批结果');
      }
      _userMsgLastUnseen = unseen;
    } catch (e) { /* 静默失败，等待下一次轮询 */ }
  }
  function startUserMsgPolling() {
    if (_userMsgPollTimer) return;
    refreshUserMsgBadge();
    _userMsgPollTimer = setInterval(refreshUserMsgBadge, 20000);
  }
  function stopUserMsgPolling() {
    if (_userMsgPollTimer) { clearInterval(_userMsgPollTimer); _userMsgPollTimer = null; }
    _userMsgLastUnseen = null;
    const badge = $('dmUserMsgBadge');
    if (badge) badge.classList.remove('show');
  }

  // ======== MCP 申请 ========
  async function renderAdminMcpRequests() {
    const keyword = state.admin.mcpReqKeyword || '';
    const status = state.admin.mcpReqStatus || 'pending';
    let data = {items:[], stats:{all:0,pending:0,approved:0,rejected:0}};
    try {
      data = await apiFetch('/api/admin/mcp-export-request/list?status=' + encodeURIComponent(status) + '&keyword=' + encodeURIComponent(keyword) + '&limit=200', {method:'POST'});
    } catch (e) {
      $('mainContent').innerHTML = `<div class="admin-panel"><p style="color:#dc2626;">加载 MCP 申请失败: ${escapeHtml(e.message)}</p></div>`;
      return;
    }
    const items = data.items || [];
    const stats = data.stats || {all:0,pending:0,approved:0,rejected:0};
    function statusBadge(value) {
      if (value === 'pending') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fef9c3;color:#854d0e;">待处理</span>';
      if (value === 'approved') return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#dcfce7;color:#166534;">已批准</span>';
      return '<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:#fee2e2;color:#b91c1c;">已驳回</span>';
    }
    $('mainContent').innerHTML = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">MCP 申请</h1><p class="text-sm text-slate-500 mt-1">普通用户在无权限时提交的 MCP 导出申请，批准后会自动授予该数据源的用户级权限。</p></div>
    <div class="admin-panel">
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;" class="dm-metrics">
        ${renderMetricCard(stats.all||0, '总申请数')}
        ${renderMetricCard(stats.pending||0, '待处理')}
        ${renderMetricCard(stats.approved||0, '已批准')}
        ${renderMetricCard(stats.rejected||0, '已驳回')}
      </div>
    </div>
    <div class="admin-panel">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;">
        <input class="dm-search" id="dmMcpReqKeyword" placeholder="按用户名、工号、部门或数据源搜索" value="${escapeHtml(keyword)}" style="flex:1;min-width:260px;max-width:420px;">
        <select class="dm-input" id="dmMcpReqStatus" style="max-width:160px;">
          <option value="pending" ${status==='pending'?'selected':''}>仅看待处理</option>
          <option value="approved" ${status==='approved'?'selected':''}>仅看已批准</option>
          <option value="rejected" ${status==='rejected'?'selected':''}>仅看已驳回</option>
          <option value="all" ${status==='all'?'selected':''}>全部状态</option>
        </select>
        <button class="dm-btn primary" id="dmMcpReqRefresh">刷新</button>
      </div>
      <div style="overflow:auto;">
        <table class="dm-table">
          <thead><tr><th>ID</th><th>申请人</th><th>数据源</th><th>申请原因</th><th>申请时间</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${items.length ? items.map(item=>`
            <tr>
              <td>${item.id}</td>
              <td style="white-space:normal;min-width:160px;">
                <div style="font-weight:600;color:#0f172a;">${escapeHtml(item.username || '')}</div>
                <div style="font-size:11px;color:#64748b;">工号: ${escapeHtml(item.employee_no || '-')} · 部门: ${escapeHtml(item.department || '-')}</div>
              </td>
              <td style="white-space:normal;min-width:150px;">${escapeHtml(item.source_name || item.source_key || '-')}</td>
              <td style="white-space:normal;min-width:220px;font-size:12px;color:#334155;">${escapeHtml(item.reason || '-')}</td>
              <td style="white-space:normal;min-width:140px;font-size:11px;color:#64748b;">${escapeHtml(item.created_at || '-')}</td>
              <td style="white-space:normal;min-width:150px;">
                ${statusBadge(item.status)}
                ${item.handled_by ? `<div style="font-size:11px;color:#64748b;margin-top:5px;">处理人: ${escapeHtml(item.handled_by)}</div>` : ''}
                ${item.admin_comment ? `<div style="font-size:11px;color:#94a3b8;margin-top:4px;">备注: ${escapeHtml(item.admin_comment)}</div>` : ''}
              </td>
              <td>
                ${item.status === 'pending'
                  ? `<div style="display:flex;gap:6px;">
                       <button class="dm-tbl-btn" data-mcp-req-approve="${item.id}" data-mcp-req-user="${escapeHtml(item.username||'')}">批准</button>
                       <button class="dm-tbl-btn danger" data-mcp-req-reject="${item.id}" data-mcp-req-user="${escapeHtml(item.username||'')}">驳回</button>
                     </div>`
                  : '<span style="font-size:11px;color:#94a3b8;">-</span>'}
              </td>
            </tr>`).join('') : '<tr><td colspan="7" style="text-align:center;color:#94a3b8;padding:20px;">暂无符合条件的申请记录</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;

    $('dmMcpReqRefresh').addEventListener('click', ()=>{
      state.admin.mcpReqKeyword = $('dmMcpReqKeyword').value.trim();
      state.admin.mcpReqStatus = $('dmMcpReqStatus').value;
      renderAdminMcpRequests();
    });
    $('dmMcpReqKeyword').addEventListener('keydown', (e)=>{
      if (e.key === 'Enter') {
        state.admin.mcpReqKeyword = $('dmMcpReqKeyword').value.trim();
        state.admin.mcpReqStatus = $('dmMcpReqStatus').value;
        renderAdminMcpRequests();
      }
    });
    $('dmMcpReqStatus').addEventListener('change', ()=>{
      state.admin.mcpReqKeyword = $('dmMcpReqKeyword').value.trim();
      state.admin.mcpReqStatus = $('dmMcpReqStatus').value;
      renderAdminMcpRequests();
    });
    document.querySelectorAll('[data-mcp-req-approve]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        const id = parseInt(btn.dataset.mcpReqApprove, 10);
        const username = btn.dataset.mcpReqUser || '';
        if (!confirm(`确认批准用户 ${username} 的 MCP 导出申请？批准后将自动授予该数据源权限。`)) return;
        try {
          const resp = await apiFetch('/api/admin/mcp-export-request/' + id + '/handle', {
            method:'POST', body: JSON.stringify({status:'approved'}),
          });
          showToast(resp.message || '已批准');
          await renderAdminMcpRequests();
          await refreshMcpRequestBadge();
        } catch(e) { showToast(e.message, true); }
      });
    });
    document.querySelectorAll('[data-mcp-req-reject]').forEach(btn=>{
      btn.addEventListener('click', async ()=>{
        const id = parseInt(btn.dataset.mcpReqReject, 10);
        const username = btn.dataset.mcpReqUser || '';
        const reason = prompt(`驳回用户 ${username} 的 MCP 导出申请，请填写驳回原因：`, '');
        if (reason === null) return;
        if (!reason.trim()) { showToast('驳回需要填写原因', true); return; }
        try {
          const resp = await apiFetch('/api/admin/mcp-export-request/' + id + '/handle', {
            method:'POST', body: JSON.stringify({status:'rejected', admin_comment: reason.trim()}),
          });
          showToast(resp.message || '已驳回');
          await renderAdminMcpRequests();
          await refreshMcpRequestBadge();
        } catch(e) { showToast(e.message, true); }
      });
    });
  }

  let _mcpRequestPollTimer = null;
  let _mcpRequestLastPending = null;
  async function refreshMcpRequestBadge() {
    if (!state.user || state.user.role !== 'admin') return;
    try {
      const r = await apiFetch('/api/admin/mcp-export-request/summary', {method:'POST'});
      const pending = r.pending || 0;
      const badge = $('dmMcpReqBadge');
      if (badge) {
        badge.textContent = pending > 99 ? '99+' : String(pending);
        badge.classList.toggle('show', pending > 0);
      }
      if (_mcpRequestLastPending !== null && pending > _mcpRequestLastPending) {
        showToast('有新的 MCP 导出申请待处理');
      }
      _mcpRequestLastPending = pending;
    } catch (e) { /* 静默失败，等待下一次轮询 */ }
  }
  function startMcpRequestPolling() {
    if (_mcpRequestPollTimer) return;
    refreshMcpRequestBadge();
    _mcpRequestPollTimer = setInterval(refreshMcpRequestBadge, 20000);
  }
  function stopMcpRequestPolling() {
    if (_mcpRequestPollTimer) { clearInterval(_mcpRequestPollTimer); _mcpRequestPollTimer = null; }
    _mcpRequestLastPending = null;
    const badge = $('dmMcpReqBadge');
    if (badge) badge.classList.remove('show');
  }

  // ======== 审计日志 ========
  function fmtAuditAction(action) {
    return AUDIT_ACTION_LABELS[action] || action;
  }
  function fmtAuditRole(role) {
    return ROLE_LABELS[role] || role;
  }
  function formatAuditDetail(l) {
    if (l.action === 'mcp_query' || l.action === 'mcp_tool_call') {
      const parts = [];
      if (l.token_id) parts.push(`令牌ID: ${l.token_id}`);
      if (l.jti) parts.push(`JTI: ${l.jti}`);
      if (l.source_name) parts.push(`数据源: ${escapeHtml(l.source_name)}`);
      if (l.keyword) parts.push(`关键词: ${escapeHtml(l.keyword)}`);
      if (l.as_of) parts.push(`历史版本: ${escapeHtml(l.as_of)}`);
      if (l.page != null && l.page_size != null) parts.push(`分页: ${l.page} / 每页${l.page_size}`);
      if (l.row_count != null) parts.push(`本页行数: ${l.row_count}`);
      if (l.total_count != null) parts.push(`总行数: ${l.total_count}`);
      if (l.search_fields) parts.push(`搜索字段: ${escapeHtml(l.search_fields)}`);
      if (l.accessed_fields) parts.push(`返回字段: ${escapeHtml(l.accessed_fields)}`);
      if (l.employee_no || l.department) {
        parts.push(`身份: ${escapeHtml(l.employee_no || '-')} / ${escapeHtml(l.department || '-')}`);
      }
      return parts.join('；');
    }
    if (l.action === 'view_data') {
      const parts = [];
      if (l.source_name) parts.push(`数据源: ${escapeHtml(l.source_name)}`);
      if (l.keyword) parts.push(`关键词: ${escapeHtml(l.keyword)}`);
      if (l.as_of) parts.push(`历史版本: ${escapeHtml(l.as_of)}`);
      if (l.page != null && l.page_size != null) parts.push(`分页: ${l.page} / 每页${l.page_size}`);
      if (l.total_count != null) parts.push(`总行数: ${l.total_count}`);
      return parts.length ? parts.join('；') : escapeHtml(l.detail || '');
    }
    return escapeHtml(l.detail || '');
  }
  function renderAdminAudit() {
    const logs = state.admin.auditLogs;
    const html = `
    <div class="mb-5"><h1 class="text-xl font-bold text-slate-800">审计日志</h1><p class="text-sm text-slate-500 mt-1">全操作记录，可按关键词筛选</p></div>
    <div class="admin-panel">
      <div style="display:flex;gap:8px;align-items:center;">
        <input class="dm-search" id="dmAuditKeyword" placeholder="筛选审计日志" value="${escapeHtml(state.admin.auditKeyword||'')}" style="flex:1;max-width:320px;">
        <button class="dm-btn primary" id="dmAuditSearch">搜索</button>
        <button class="dm-btn" id="dmAuditExport">导出 CSV</button>
      </div>
    </div>
    <div class="admin-panel">
      <h3>操作记录（最近 ${logs.length} 条）</h3>
      <div style="overflow:auto;"><table class="dm-table">
        <thead><tr><th>时间</th><th>用户</th><th>角色</th><th>操作</th><th>目标</th><th>详情</th><th>IP</th></tr></thead>
        <tbody>${logs.map(l=>`<tr>
          <td>${l.created_at||''}</td><td>${escapeHtml(l.username)}</td><td>${fmtAuditRole(l.role)}</td><td>${fmtAuditAction(l.action)}</td>
          <td>${escapeHtml(l.target)}</td><td>${formatAuditDetail(l)}</td><td>${l.ip||''}</td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>`;
    $('mainContent').innerHTML = html;
    $('dmAuditSearch').addEventListener('click', async ()=>{
      state.admin.auditKeyword = $('dmAuditKeyword').value.trim();
      await loadAdminData(); renderAdminAudit();
    });
    $('dmAuditExport').addEventListener('click', async ()=>{
      try {
        const kw = encodeURIComponent(state.admin.auditKeyword||'');
        const resp = await fetch(API_BASE+'/api/admin/audit-log/export?keyword='+kw,{
          headers:{'Authorization':'Bearer '+state.token}
        });
        if (!resp.ok) throw new Error('导出失败');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = 'audit_log.csv'; a.click();
        URL.revokeObjectURL(url); showToast('导出成功');
      } catch(e) { showToast(e.message, true); }
    });
  }

  // ======== 初始化 ========
  async function init() {
    await loadPublicConfig();

    // 已取消 URL 参数自动登录，改为用户手动输入账号密码登录
    // if (!state.token) {
    //   await _tryUrlSsoLogin();
    // }

    if (features.devSsoEnabled) {
      const urlParams = new URLSearchParams(window.location.search);
      const ssoToken = urlParams.get('token') || urlParams.get('sso_token');
      if (ssoToken) {
        try {
          const resp = await fetch(API_BASE + '/api/auth/sso?token=' + encodeURIComponent(ssoToken));
          if (resp.ok) {
            const data = await resp.json();
            state.token = data.token; state.user = data.user;
            localStorage.setItem('datamid_token', state.token);
            history.replaceState({}, '', window.location.pathname);
          }
        } catch(e) {}
      }
    }
    if (features.yanhuangSsoEnabled && !state.token) {
      // 炎黄跳转过来只负责自动开号，不负责免密登录：账号确认就绪后，把用户名帮用户填好，
      // 但还是要落到登录页让他自己输一遍密码——这是刻意保留的，不是漏做了自动登录。
      const urlParams = new URLSearchParams(window.location.search);
      const yhEmployeeNo = urlParams.get('yh_employee_no');
      if (yhEmployeeNo) {
        try {
          const qs = new URLSearchParams({
            employee_no: yhEmployeeNo,
            full_name: urlParams.get('yh_full_name') || '',
            ts: urlParams.get('yh_ts') || '',
            sig: urlParams.get('yh_sig') || '',
          });
          const resp = await fetch(API_BASE + '/api/auth/sso/yanhuang?' + qs.toString());
          history.replaceState({}, '', window.location.pathname);
          if (resp.ok) {
            const data = await resp.json();
            const authUserEl = $('dmAuthUser');
            if (authUserEl) authUserEl.value = data.username || '';
            if (data.is_new) {
              showToast('账号已自动开通，首次登录密码为：工号 + 工号后三位，登录后请立即修改');
            } else {
              showToast('账号已识别，请输入密码登录');
            }
          } else {
            const body = await resp.json().catch(()=>({}));
            showToast('炎黄登录失败：' + (body.detail || '请重新从炎黄平台跳转'), true);
          }
        } catch(e) {}
      }
    }
    if (state.token) await loadUser().catch(()=>{});
    updateLoginUI();
    if (state.user) {
      loadDynamicPlatforms().catch(()=>{});
      // 已取消登录后强制改密
      // _checkForcePasswordChange();
    }
    if (state.user && state.user.role === 'admin') {
      loadAdminData().catch(()=>{});
    }
  }
  // ======== 字段级权限 UI ========
  async function openFieldRestrictionEditor(sourceKey, sourceName) {
    let ov = document.getElementById('dmFieldRestrictOverlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'dmFieldRestrictOverlay';
    ov.className = 'dm-mask';
    ov.style.zIndex = '9999';
    ov.innerHTML = `<div class="dm-modal dm-modal-light" style="width:min(1280px,calc(100vw - 48px));max-height:min(84vh,780px);aspect-ratio:16 / 9;display:flex;flex-direction:column;">
      <div class="dm-modal-head"><div><h2>字段权限设置</h2><p>${escapeHtml(sourceName || sourceKey)}</p></div><button class="dm-close" type="button" id="dmFrClose">×</button></div>
      <div class="dm-modal-body" id="dmFrBody" style="flex:1;overflow:hidden;display:flex;flex-direction:column;">加载中…</div></div>`;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    ov.querySelector('#dmFrClose').addEventListener('click', close);
    ov.addEventListener('click', e => { if (e.target === ov) close(); });
    try {
      const r = await apiFetch('/api/admin/datasource/' + encodeURIComponent(sourceKey) + '/fields');
      const fields = r.items || [];
      const cards = fields.map(f => {
        const acc = f.restricted_access || 'hide';
        return `<div data-field="${escapeHtml(f.field_name)}" style="display:flex;flex-direction:column;gap:12px;border:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;background:#fff;box-shadow:0 10px 24px rgba(15,23,42,0.05);">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;">
            <div style="min-width:0;">
              <div style="font-size:14px;font-weight:700;color:#0f172a;line-height:1.45;">${escapeHtml(f.field_label || f.field_name)}</div>
              <div style="margin-top:4px;color:#94a3b8;font-size:11px;font-family:monospace;word-break:break-all;">${escapeHtml(f.field_name)}</div>
            </div>
            <label style="display:inline-flex;align-items:center;gap:6px;background:${f.is_restricted ? '#fef2f2' : '#f8fafc'};border:1px solid ${f.is_restricted ? '#fecaca' : '#e2e8f0'};border-radius:999px;padding:5px 10px;color:${f.is_restricted ? '#b91c1c' : '#64748b'};font-size:12px;white-space:nowrap;">
              <input type="checkbox" class="fr-restrict" ${f.is_restricted ? 'checked' : ''} style="accent-color:#ef4444;">
              受限字段
            </label>
          </div>
          <div style="display:grid;grid-template-columns:110px 1fr;gap:10px;align-items:center;">
            <div style="font-size:12px;color:#64748b;">处置方式</div>
            <select class="fr-access" style="width:100%;background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:12px;">
              <option value="hide"${acc === 'hide' ? ' selected' : ''}>隐藏</option>
              <option value="mask"${acc === 'mask' ? ' selected' : ''}>脱敏</option>
            </select>
            <div style="font-size:12px;color:#64748b;">脱敏规则</div>
            <input class="fr-mask" value="${escapeHtml(f.mask_rule || '')}" placeholder="留空=全掩码；last4=保留后4位" style="width:100%;background:#fff;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;font-size:12px;">
          </div>
          <div style="font-size:11px;color:#64748b;line-height:1.6;">勾选后，该字段默认对非管理员隐藏或脱敏；是否可见再到“用户列表 → 分配权限”里按用户或部门授权。</div>
        </div>`;
      }).join('');
      ov.querySelector('#dmFrBody').innerHTML = `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px;">
          <p style="flex:1;color:#64748b;font-size:12px;margin:0;line-height:1.7;">勾选“受限字段”后，该列默认对普通用户隐藏或脱敏。未勾选表示只要有该数据源权限就能看到。管理员始终可见全部字段。</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;">
            <span style="display:inline-flex;align-items:center;gap:6px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:6px 10px;color:#64748b;font-size:11px;">隐藏：完全不可见</span>
            <span style="display:inline-flex;align-items:center;gap:6px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:6px 10px;color:#64748b;font-size:11px;">脱敏：展示掩码值</span>
          </div>
        </div>
        <div style="flex:1;overflow:auto;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc;padding:16px;">
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px;align-items:start;">
            ${cards || '<div style="padding:20px;color:#94a3b8;font-size:13px;border:1px dashed #cbd5e1;border-radius:12px;background:#fff;">暂无字段（请先同步数据）</div>'}
          </div>
        </div>
        <div style="margin-top:14px;display:flex;justify-content:flex-end;gap:10px;padding-top:14px;border-top:1px solid #e2e8f0;">
          <button class="dm-btn" id="dmFrCancel">取消</button>
          <button class="dm-btn primary" id="dmFrSave">保存</button>
        </div>`;
      ov.querySelector('#dmFrCancel').addEventListener('click', close);
      ov.querySelector('#dmFrSave').addEventListener('click', async () => {
        const trs = [...ov.querySelectorAll('[data-field]')];
        try {
          for (const tr of trs) {
            await apiFetch('/api/admin/datasource/' + encodeURIComponent(sourceKey) + '/field-restriction', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                source_key: sourceKey,
                field_name: tr.getAttribute('data-field'),
                is_restricted: tr.querySelector('.fr-restrict').checked,
                restricted_access: tr.querySelector('.fr-access').value,
                mask_rule: tr.querySelector('.fr-mask').value.trim(),
              }),
            });
          }
          showToast('字段权限已保存'); close();
        } catch (e) { showToast(e.message, true); }
      });
    } catch (e) { ov.querySelector('#dmFrBody').innerHTML = '<p style="color:#f87171;">加载失败: ' + escapeHtml(e.message) + '</p>'; }
  }

  async function renderUserFieldGrants(uid, sources) {
    const box = document.getElementById('dmUserFieldGrantBox');
    if (!box) return;
    const opts = (sources || []).map(s => `<option value="${escapeHtml(s.source_key)}">${escapeHtml(s.source_name)}</option>`).join('');
    box.innerHTML = `
      <div style="margin-top:18px;border-top:1px solid #e2e8f0;padding-top:14px;">
        <label style="display:block;color:#475569;font-size:13px;margin-bottom:6px;font-weight:600;">字段级授权（仅对已设为“受限”的列生效）</label>
        <select id="dmUfgSource" style="width:100%;padding:9px 12px;border:1px solid #cbd5e1;border-radius:8px;background:#fff;color:#0f172a;"><option value="">— 选择数据源 —</option>${opts}</select>
        <div id="dmUfgFields" style="margin-top:10px;"></div>
      </div>`;
    const sel = document.getElementById('dmUfgSource');
    sel.addEventListener('change', async () => {
      const sk = sel.value; const target = document.getElementById('dmUfgFields');
      if (!sk) { target.innerHTML = ''; return; }
      target.innerHTML = '<span style="color:#94a3b8;font-size:12px;">加载中…</span>';
      try {
        const r = await apiFetch('/api/admin/user/' + uid + '/field-permissions?source_key=' + encodeURIComponent(sk));
        const restricted = r.restricted_fields || []; const granted = new Set(r.granted_fields || []);
        if (!restricted.length) { target.innerHTML = '<span style="color:#94a3b8;font-size:12px;">该数据源暂无受限字段。可在"数据源管理 → 编辑 → 字段权限"中设置。</span>'; return; }
        target.innerHTML = `<div style="border:1px solid #e2e8f0;border-radius:12px;background:#fff;padding:12px 14px;">${
          restricted.map(f => `<label style="display:inline-flex;align-items:center;gap:6px;margin:4px 14px 4px 0;color:#0f172a;font-size:13px;"><input type="checkbox" class="ufg-field" value="${escapeHtml(f)}" ${granted.has(f) ? 'checked' : ''} style="accent-color:#3b82f6;">${escapeHtml(f)}</label>`).join('')
        }</div>`
          + `<div style="margin-top:10px;"><button class="dm-btn primary" id="dmUfgSave">保存字段授权</button></div>`;
        document.getElementById('dmUfgSave').addEventListener('click', async () => {
          const fields = [...target.querySelectorAll('input.ufg-field:checked')].map(cb => cb.value);
          try {
            await apiFetch('/api/admin/user/' + uid + '/field-permissions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_key: sk, field_names: fields }) });
            showToast('字段授权已保存');
          } catch (e) { showToast(e.message, true); }
        });
      } catch (e) { target.innerHTML = '<span style="color:#f87171;font-size:12px;">加载失败: ' + escapeHtml(e.message) + '</span>'; }
    });
  }

  $('dmDeFieldPerm').addEventListener('click', () => { if (_currentDsEdit) openFieldRestrictionEditor(_currentDsEdit.source_key, _currentDsEdit.source_name); });

  init();
})();
