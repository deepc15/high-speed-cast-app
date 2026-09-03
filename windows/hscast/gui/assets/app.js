/**
 * HSCast Windows Application - Interactive Frontend Controller
 */

(() => {
  'use strict';

  // --- State Management ---
  const state = {
    mode: 'mirror',       // 'mirror' | 'desktop'
    connType: 'usb',      // 'usb' | 'wifi'
    sessionState: 'idle', // 'idle' | 'starting' | 'running' | 'error'
    sessionUptime: 0,
    lastLogId: 0,
    isDemoRunning: false,
    pollInterval: null,
    devices: [],
    monitors: [],
    localIps: [],
    config: {},
  };

  // --- DOM Elements ---
  const el = {
    // Navigation
    tabs: document.querySelectorAll('.nav-tab'),
    panes: document.querySelectorAll('.tab-pane'),
    docBadge: document.getElementById('header-doctor-badge'),
    sessionPill: document.getElementById('session-status-pill'),
    sessionPillText: document.getElementById('session-status-text'),

    // Mode & Connection
    btnModeMirror: document.getElementById('btn-mode-mirror'),
    btnModeDesktop: document.getElementById('btn-mode-desktop'),
    btnConnUsb: document.getElementById('btn-conn-usb'),
    btnConnWifi: document.getElementById('btn-conn-wifi'),
    targetUsbArea: document.getElementById('target-usb-area'),
    targetWifiArea: document.getElementById('target-wifi-area'),
    targetMonitorArea: document.getElementById('target-monitor-area'),
    selectDevice: document.getElementById('select-device'),
    btnRefreshDevices: document.getElementById('btn-refresh-devices'),
    inputPhoneIp: document.getElementById('input-phone-ip'),
    pcLocalIp: document.getElementById('pc-local-ip'),
    selectMonitor: document.getElementById('select-monitor'),

    // Primary Action & Demo
    btnStart: document.getElementById('btn-start-cast'),
    btnStop: document.getElementById('btn-stop-cast'),
    startBtnLabel: document.getElementById('start-btn-label'),
    btnRunDemo: document.getElementById('btn-run-demo'),
    demoBtnLabel: document.getElementById('demo-btn-label'),

    // HUD & Telemetry
    hudUptime: document.getElementById('hud-uptime-badge'),
    hudFps: document.getElementById('hud-fps-val'),
    hudBitrate: document.getElementById('hud-bitrate-val'),
    hudCodec: document.getElementById('hud-codec-val'),
    hudHw: document.getElementById('hud-hw-val'),

    // Remote Actions
    btnActionBack: document.getElementById('btn-action-back'),
    btnActionHome: document.getElementById('btn-action-home'),
    btnActionRecents: document.getElementById('btn-action-recents'),
    btnActionLock: document.getElementById('btn-action-lock'),
    btnActionWake: document.getElementById('btn-action-wake'),

    // Tuning & Toggles
    presetPills: document.querySelectorAll('.preset-pill'),
    sliderBitrate: document.getElementById('slider-bitrate'),
    sliderBitrateLabel: document.getElementById('slider-bitrate-label'),
    selectFps: document.getElementById('select-fps'),
    selectCodec: document.getElementById('select-codec'),
    toggleHwaccel: document.getElementById('toggle-hwaccel'),
    toggleControl: document.getElementById('toggle-control'),
    toggleVsync: document.getElementById('toggle-vsync'),
    toggleCursor: document.getElementById('toggle-cursor'),

    // Console
    btnToggleConsole: document.getElementById('btn-toggle-console'),
    consoleBody: document.getElementById('console-body'),
    consoleLogs: document.getElementById('console-logs'),
    logCountBadge: document.getElementById('log-count-badge'),
    btnCopyLogs: document.getElementById('btn-copy-logs'),
    btnClearLogs: document.getElementById('btn-clear-logs'),

    // Doctor
    btnRerunDoctor: document.getElementById('btn-rerun-doctor'),
    docPyBadge: document.getElementById('doc-py-badge'),
    docPyDesc: document.getElementById('doc-py-desc'),
    docPyavBadge: document.getElementById('doc-pyav-badge'),
    docPyavDesc: document.getElementById('doc-pyav-desc'),
    docEncBadge: document.getElementById('doc-encoders-badge'),
    docEncDesc: document.getElementById('doc-encoders-desc'),
    docSdlBadge: document.getElementById('doc-sdl-badge'),
    docSdlDesc: document.getElementById('doc-sdl-desc'),
    docDxcamBadge: document.getElementById('doc-dxcam-badge'),
    docDxcamDesc: document.getElementById('doc-dxcam-desc'),
    docAdbBadge: document.getElementById('doc-adb-badge'),
    docAdbDesc: document.getElementById('doc-adb-desc'),
    inputCustomAdb: document.getElementById('input-custom-adb'),
    btnBrowseAdb: document.getElementById('btn-browse-adb'),
    btnSaveAdb: document.getElementById('btn-save-adb'),

    // Preferences
    inputRecordPath: document.getElementById('input-record-path'),
    btnBrowseRecord: document.getElementById('btn-browse-record'),
    btnClearRecord: document.getElementById('btn-clear-record'),
    selectFilter: document.getElementById('select-filter'),
    btnSavePrefs: document.getElementById('btn-save-prefs'),
    prefsSavedMsg: document.getElementById('prefs-saved-msg'),

    // Toast
    toastContainer: document.getElementById('toast-container'),

    // Error Modal
    errorModal: document.getElementById('error-modal'),
    btnModalClose: document.getElementById('btn-modal-close'),
    btnModalCloseIcon: document.getElementById('btn-modal-close-icon'),
    errorModalTitle: document.getElementById('error-modal-title'),
    errorModalSubtitle: document.getElementById('error-modal-subtitle'),
    errorModalMessage: document.getElementById('error-modal-message'),
    errorModalHint: document.getElementById('error-modal-hint'),
    errorModalSteps: document.getElementById('error-modal-steps'),
  };

  // --- API Helper ---
  async function api(path, options = {}) {
    try {
      const res = await fetch(`/api${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      });
      return await res.json();
    } catch (err) {
      return { ok: false, error: err.message };
    }
  }

  // --- Toast Notifications ---
  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    el.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      setTimeout(() => toast.remove(), 250);
    }, 3200);
  }

  // --- Interactive Error Modal ---
  function showErrorModal(message, options = {}) {
    if (!message || !el.errorModal) return;
    const msg = String(message).trim();

    el.errorModalMessage.textContent = msg;

    if (msg.toLowerCase().includes('please select usb option in android')) {
      el.errorModalTitle.textContent = 'Connection Mode Mismatch';
      el.errorModalSubtitle.textContent = 'Android device is set to Wi-Fi mode while PC is in USB mode';
      el.errorModalSteps.innerHTML = `
        <li>Open the <strong>HSCast</strong> app on your Android device.</li>
        <li>Under <strong>Mode</strong>, select <strong>USB</strong>.</li>
        <li>Then click <strong>Start Mirroring</strong> on PC to proceed.</li>
      `;
    } else if (msg.toLowerCase().includes('please select wi-fi option in android') || msg.toLowerCase().includes('please select wifi option in android')) {
      el.errorModalTitle.textContent = 'Connection Mode Mismatch';
      el.errorModalSubtitle.textContent = 'Android device is set to USB mode while PC is in Wi-Fi mode';
      el.errorModalSteps.innerHTML = `
        <li>Open the <strong>HSCast</strong> app on your Android device.</li>
        <li>Under <strong>Mode</strong>, select <strong>Wi-Fi</strong>.</li>
        <li>Tap <strong>Start casting to PC</strong> on your phone.</li>
        <li>Then click <strong>Start Mirroring</strong> on PC to proceed.</li>
      `;
    } else {
      el.errorModalTitle.textContent = options.title || 'Connection Notice';
      el.errorModalSubtitle.textContent = options.subtitle || 'Action required to proceed';
      el.errorModalSteps.innerHTML = `
        <li>Make sure your phone is unlocked and connected.</li>
        <li>Check that <strong>HSCast</strong> is open on your device.</li>
        <li>Verify your USB cable or Wi-Fi network connection.</li>
      `;
    }

    el.errorModal.classList.remove('hidden');
    setTimeout(() => {
      if (el.btnModalClose) el.btnModalClose.focus();
    }, 50);
  }

  function closeErrorModal() {
    if (!el.errorModal) return;
    el.errorModal.classList.add('hidden');
    if (el.btnStart) el.btnStart.focus();
  }

  // --- Tab Navigation ---
  function initTabs() {
    el.tabs.forEach(btn => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tab;
        el.tabs.forEach(t => {
          t.classList.remove('active');
          t.setAttribute('aria-selected', 'false');
        });
        el.panes.forEach(p => p.classList.remove('active'));

        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        const activePane = document.getElementById(`tab-${target}`);
        if (activePane) activePane.classList.add('active');

        if (target === 'doctor') {
          refreshDoctor();
        }
      });
    });
  }

  // --- Mode & Connection Handlers ---
  function setMode(mode) {
    state.mode = mode;
    if (mode === 'mirror') {
      el.btnModeMirror.classList.add('active');
      el.btnModeDesktop.classList.remove('active');
      el.startBtnLabel.textContent = 'Start Mirroring';
      el.targetMonitorArea.classList.add('hidden');
    } else {
      el.btnModeDesktop.classList.add('active');
      el.btnModeMirror.classList.remove('active');
      el.startBtnLabel.textContent = 'Start Desktop Cast';
      el.targetMonitorArea.classList.remove('hidden');
    }
    updateHudLabels();
    saveCurrentConfig();
  }

  function setConnType(type) {
    state.connType = type;
    if (type === 'usb') {
      el.btnConnUsb.classList.add('active');
      el.btnConnWifi.classList.remove('active');
      el.targetUsbArea.classList.remove('hidden');
      el.targetWifiArea.classList.add('hidden');
    } else {
      el.btnConnWifi.classList.add('active');
      el.btnConnUsb.classList.remove('active');
      el.targetUsbArea.classList.add('hidden');
      el.targetWifiArea.classList.remove('hidden');
    }
    saveCurrentConfig();
  }

  // --- Presets ---
  const presets = {
    gaming: { bitrate: 16, fps: 60, codec: 'h264', hwaccel: true, vsync: false },
    balanced: { bitrate: 12, fps: 60, codec: 'h264', hwaccel: true, vsync: false },
    cinema: { bitrate: 22, fps: 60, codec: 'hevc', hwaccel: true, vsync: true },
  };

  function applyPreset(name) {
    const p = presets[name];
    if (!p) return;

    el.sliderBitrate.value = p.bitrate;
    el.sliderBitrateLabel.textContent = `${p.bitrate} Mbps`;
    el.selectFps.value = String(p.fps);
    el.selectCodec.value = p.codec;
    el.toggleHwaccel.checked = p.hwaccel;
    el.toggleVsync.checked = p.vsync;

    el.presetPills.forEach(pill => {
      pill.classList.toggle('active', pill.dataset.preset === name);
    });

    updateHudLabels();
    saveCurrentConfig();
    showToast(`Applied ${name.toUpperCase()} preset`, 'success');
  }

  function updateHudLabels() {
    el.hudFps.textContent = `${el.selectFps.value} FPS`;
    el.hudBitrate.textContent = `${el.sliderBitrate.value}.0 Mb/s`;
    el.hudCodec.textContent = el.selectCodec.value.toUpperCase();
    el.hudHw.textContent = el.toggleHwaccel.checked ? 'Active' : 'Software';
    el.hudHw.className = `telemetry-value ${el.toggleHwaccel.checked ? 'text-cyan' : ''}`;
  }

  // --- Devices & Network Refresh ---
  async function refreshDevices() {
    el.selectDevice.innerHTML = '<option value="">Scanning devices...</option>';
    const res = await api('/devices');
    if (res.ok && res.devices) {
      state.devices = res.devices;
      if (res.devices.length === 0) {
        el.selectDevice.innerHTML = '<option value="">No USB devices detected (Connect phone & enable USB Debugging)</option>';
      } else {
        el.selectDevice.innerHTML = res.devices.map(d => {
          const label = `${d.model} (${d.serial}) [${d.status}]`;
          return `<option value="${d.serial}">${label}</option>`;
        }).join('');
      }
    } else {
      el.selectDevice.innerHTML = '<option value="">ADB not found. Set path in System Doctor.</option>';
    }
  }

  async function refreshMonitors() {
    const res = await api('/monitors');
    if (res.ok && res.monitors) {
      state.monitors = res.monitors;
      el.selectMonitor.innerHTML = res.monitors.map(m => {
        return `<option value="${m.index}">${m.name}</option>`;
      }).join('');
    }
  }

  async function refreshNetwork() {
    const res = await api('/network');
    if (res.ok && res.ips && res.ips.length > 0) {
      state.localIps = res.ips;
      el.pcLocalIp.textContent = res.ips[0];
    }
  }

  // --- Session Control ---
  async function startCast() {
    const params = {
      mode: state.mode,
      conn_type: state.connType,
      serial: el.selectDevice.value || null,
      phone_ip: el.inputPhoneIp.value.trim(),
      fps: parseInt(el.selectFps.value, 10) || 60,
      bitrate: (parseInt(el.sliderBitrate.value, 10) || 12) * 1_000_000,
      codec: el.selectCodec.value,
      hwaccel: el.toggleHwaccel.checked,
      vsync: el.toggleVsync.checked,
      control: el.toggleControl.checked,
      cursor: el.toggleCursor.checked,
      monitor: parseInt(el.selectMonitor.value, 10) || 0,
      record: el.inputRecordPath.value.trim() || null,
      scale_filter: el.selectFilter.value || 'AREA',
    };

    if (state.connType === 'wifi' && state.mode === 'mirror' && !params.phone_ip) {
      showToast('Please enter the phone\'s Wi-Fi IP address', 'error');
      showErrorModal('Please enter the phone\'s Wi-Fi IP address before connecting.', {
        title: 'IP Address Missing',
        subtitle: 'Required for Wi-Fi mirroring',
      });
      el.inputPhoneIp.focus();
      return;
    }

    el.btnStart.disabled = true;
    el.startBtnLabel.textContent = 'Launching...';

    const res = await api('/session/start', {
      method: 'POST',
      body: JSON.stringify(params),
    });

    el.btnStart.disabled = false;
    if (res.ok) {
      updateSessionUI('running', 'Active Stream');
      showToast('Casting session started! Opening high-speed window...', 'success');
      saveCurrentConfig();
    } else {
      updateSessionUI('idle', 'Idle');
      showToast(res.error || 'Failed to start', 'error');
      showErrorModal(res.error || 'Failed to start');
    }
  }

  async function stopCast() {
    el.btnStop.disabled = true;
    const res = await api('/session/stop', { method: 'POST' });
    el.btnStop.disabled = false;
    updateSessionUI('idle', 'Idle');
    if (res.ok) {
      showToast('Session stopped cleanly', 'info');
    }
  }

  async function toggleDemo() {
    if (state.isDemoRunning) {
      await api('/demo/stop', { method: 'POST' });
      await stopCast();
      state.isDemoRunning = false;
      el.demoBtnLabel.textContent = 'Run Virtual Demo';
      showToast('Virtual Demo stopped', 'info');
    } else {
      const role = state.mode === 'mirror' ? 'sender' : 'receiver';
      el.demoBtnLabel.textContent = 'Starting Demo...';
      const demoRes = await api('/demo/start', {
        method: 'POST',
        body: JSON.stringify({ role }),
      });
      if (demoRes.ok) {
        state.isDemoRunning = true;
        el.demoBtnLabel.textContent = 'Stop Demo';
        showToast('Virtual Android started! Launching session...', 'success');

        // Automatically switch to Wi-Fi 127.0.0.1 and connect
        setConnType('wifi');
        el.inputPhoneIp.value = '127.0.0.1';
        setTimeout(() => startCast(), 600);
      } else {
        el.demoBtnLabel.textContent = 'Run Virtual Demo';
        showToast(`Demo launch failed: ${demoRes.error}`, 'error');
      }
    }
  }

  async function sendAction(action) {
    const res = await api('/action', {
      method: 'POST',
      body: JSON.stringify({ action }),
    });
    if (res.ok) {
      showToast(`Sent command: ${action.toUpperCase()}`, 'info');
    } else {
      showToast(`Command error: ${res.error}`, 'error');
    }
  }

  function updateSessionUI(sessionState, msg) {
    state.sessionState = sessionState;

    if (sessionState === 'running') {
      el.sessionPill.className = 'status-pill status-running';
      el.sessionPillText.textContent = 'Active Stream';
      el.btnStart.classList.add('hidden');
      el.btnStop.classList.remove('hidden');
    } else if (sessionState === 'starting') {
      el.sessionPill.className = 'status-pill status-starting';
      el.sessionPillText.textContent = 'Connecting...';
      el.btnStart.classList.remove('hidden');
      el.btnStop.classList.add('hidden');
    } else {
      // Normal cases when not connected -> always 'Idle'!
      el.sessionPill.className = 'status-pill status-idle';
      el.sessionPillText.textContent = 'Idle';
      el.btnStart.classList.remove('hidden');
      el.btnStop.classList.add('hidden');
      el.startBtnLabel.textContent = state.mode === 'mirror' ? 'Start Mirroring' : 'Start Desktop Cast';
      el.hudUptime.textContent = 'Uptime: 00:00';
    }
  }

  // --- Real-time Session Polling ---
  async function pollSessionState() {
    const res = await api(`/session/state?since=${state.lastLogId}`);
    if (res.ok && res.session) {
      const s = res.session;

      // Update state if changed
      if (s.state !== state.sessionState) {
        updateSessionUI(s.state, s.state_msg);
      }

      // Update uptime counter
      if (s.uptime_seconds > 0) {
        const m = String(Math.floor(s.uptime_seconds / 60)).padStart(2, '0');
        const sec = String(s.uptime_seconds % 60).padStart(2, '0');
        el.hudUptime.textContent = `Uptime: ${m}:${sec}`;
      }

      // Update demo status
      if (s.demo_running !== state.isDemoRunning) {
        state.isDemoRunning = s.demo_running;
        el.demoBtnLabel.textContent = s.demo_running ? 'Stop Demo' : 'Run Virtual Demo';
      }

      // Append new logs
      if (s.logs && s.logs.length > 0) {
        s.logs.forEach(item => {
          state.lastLogId = Math.max(state.lastLogId, item.id);
          appendLogLine(item.time, item.text, item.level);
          if (item.level === 'error' && (item.text.includes('Please select') || item.text.includes('Validation failed'))) {
            const clean = item.text.replace(/\[ERROR\]\s*/i, '').replace(/Validation failed:\s*/i, '').trim();
            showErrorModal(clean);
          }
        });
      }
    }
  }

  function appendLogLine(time, text, level) {
    const line = document.createElement('div');
    line.className = `log-line log-${level}`;
    line.innerHTML = `<span class="log-time">[${time}]</span>${escapeHtml(text)}`;
    el.consoleLogs.appendChild(line);

    // Limit DOM lines
    while (el.consoleLogs.children.length > 300) {
      el.consoleLogs.removeChild(el.consoleLogs.firstChild);
    }

    el.consoleBody.scrollTop = el.consoleBody.scrollHeight;
    el.logCountBadge.textContent = `${el.consoleLogs.children.length} lines`;
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // --- System Doctor ---
  async function refreshDoctor() {
    el.btnRerunDoctor.disabled = true;
    const res = await api('/status');
    el.btnRerunDoctor.disabled = false;

    if (!res.ok || !res.status) return;
    const s = res.status;

    // Python
    el.docPyBadge.className = `badge-status ${s.python.ok ? 'status-ok' : 'status-fail'}`;
    el.docPyBadge.textContent = s.python.ok ? 'Pass' : 'Fail';
    el.docPyDesc.textContent = `Python ${s.python.version} (${s.python.ok ? 'Compatible' : 'Needs 3.10+'})`;

    // PyAV
    el.docPyavBadge.className = `badge-status ${s.pyav.ok ? 'status-ok' : 'status-fail'}`;
    el.docPyavBadge.textContent = s.pyav.ok ? 'Pass' : 'Fail';
    el.docPyavDesc.textContent = s.pyav.ok ? `PyAV ${s.pyav.version} installed (D3D11VA hardware decode ready)` : s.pyav.version;

    // GPU Encoders
    const encOk = s.encoders && s.encoders.length > 0;
    el.docEncBadge.className = `badge-status ${encOk ? 'status-ok' : 'status-fail'}`;
    el.docEncBadge.textContent = encOk ? (s.has_gpu_encoder ? 'GPU Hardware' : 'Software Only') : 'None';
    el.docEncDesc.textContent = encOk ? s.encoders.join(', ') : 'No usable encoders probed';

    // SDL2
    el.docSdlBadge.className = `badge-status ${s.sdl2.ok ? 'status-ok' : 'status-fail'}`;
    el.docSdlBadge.textContent = s.sdl2.ok ? 'Pass' : 'Fail';
    el.docSdlDesc.textContent = s.sdl2.ok ? `PySDL2 (SDL ${s.sdl2.version}) direct YUV GPU upload` : s.sdl2.version;

    // DXGI Capture
    el.docDxcamBadge.className = `badge-status ${s.dxcam ? 'status-ok' : (s.mss ? 'status-warn' : 'status-fail')}`;
    el.docDxcamBadge.textContent = s.dxcam ? 'Pass (DXGI)' : (s.mss ? 'MSS Fallback' : 'Missing');
    el.docDxcamDesc.textContent = s.dxcam ? 'DXGI Desktop Duplication 60+ FPS hardware capture' : (s.mss ? 'MSS fallback capture available' : 'Neither dxcam nor mss installed');

    // ADB
    el.docAdbBadge.className = `badge-status ${s.adb.ok ? 'status-ok' : 'status-warn'}`;
    el.docAdbBadge.textContent = s.adb.ok ? 'Pass' : 'Not on PATH';
    el.docAdbDesc.textContent = s.adb.ok ? `ADB located at: ${s.adb.path}` : 'ADB not found. Specify custom path below or use Wi-Fi mode.';

    // Header badge dot
    if (s.adb.ok && s.pyav.ok && s.sdl2.ok) {
      el.docBadge.className = 'badge-dot dot-ok';
      el.docBadge.title = 'All core diagnostics passed';
    } else {
      el.docBadge.className = 'badge-dot dot-warn';
      el.docBadge.title = 'Check System Doctor for recommendations';
    }
  }

  // --- Configuration Sync ---
  async function loadInitialConfig() {
    const res = await api('/config');
    if (res.ok && res.config) {
      const c = res.config;
      state.config = c;

      if (c.mode) setMode(c.mode);
      if (c.conn_type) setConnType(c.conn_type);
      if (c.phone_ip) el.inputPhoneIp.value = c.phone_ip;
      if (c.bitrate) {
        const mbps = Math.round(c.bitrate / 1_000_000);
        el.sliderBitrate.value = mbps;
        el.sliderBitrateLabel.textContent = `${mbps} Mbps`;
      }
      if (c.fps) el.selectFps.value = String(c.fps);
      if (c.codec) el.selectCodec.value = c.codec;
      if (c.hwaccel !== undefined) el.toggleHwaccel.checked = c.hwaccel;
      if (c.vsync !== undefined) el.toggleVsync.checked = c.vsync;
      if (c.control !== undefined) el.toggleControl.checked = c.control;
      if (c.cursor !== undefined) el.toggleCursor.checked = c.cursor;
      if (c.custom_adb_path) el.inputCustomAdb.value = c.custom_adb_path;
      if (c.record) el.inputRecordPath.value = c.record;
      if (c.scale_filter) el.selectFilter.value = c.scale_filter;

      updateHudLabels();
    }
  }

  async function saveCurrentConfig() {
    const updates = {
      mode: state.mode,
      conn_type: state.connType,
      phone_ip: el.inputPhoneIp.value.trim(),
      serial: el.selectDevice.value || '',
      bitrate: (parseInt(el.sliderBitrate.value, 10) || 12) * 1_000_000,
      fps: parseInt(el.selectFps.value, 10) || 60,
      codec: el.selectCodec.value,
      hwaccel: el.toggleHwaccel.checked,
      vsync: el.toggleVsync.checked,
      control: el.toggleControl.checked,
      cursor: el.toggleCursor.checked,
      custom_adb_path: el.inputCustomAdb.value.trim(),
      record: el.inputRecordPath.value.trim(),
      scale_filter: el.selectFilter.value,
    };
    await api('/config', {
      method: 'POST',
      body: JSON.stringify(updates),
    });
  }

  // --- Event Listeners Setup ---
  function initListeners() {
    // Mode switcher
    el.btnModeMirror.addEventListener('click', () => setMode('mirror'));
    el.btnModeDesktop.addEventListener('click', () => setMode('desktop'));

    // Connection switcher
    el.btnConnUsb.addEventListener('click', () => setConnType('usb'));
    el.btnConnWifi.addEventListener('click', () => setConnType('wifi'));

    // Presets
    el.presetPills.forEach(pill => {
      pill.addEventListener('click', () => applyPreset(pill.dataset.preset));
    });

    // Slider
    el.sliderBitrate.addEventListener('input', () => {
      el.sliderBitrateLabel.textContent = `${el.sliderBitrate.value} Mbps`;
      updateHudLabels();
    });
    el.sliderBitrate.addEventListener('change', saveCurrentConfig);

    // Dropdowns & Toggles
    [el.selectFps, el.selectCodec].forEach(s => {
      s.addEventListener('change', () => {
        updateHudLabels();
        saveCurrentConfig();
      });
    });

    [el.toggleHwaccel, el.toggleControl, el.toggleVsync, el.toggleCursor].forEach(cb => {
      cb.addEventListener('change', () => {
        updateHudLabels();
        saveCurrentConfig();
      });
    });

    // Action buttons
    el.btnStart.addEventListener('click', startCast);
    el.btnStop.addEventListener('click', stopCast);
    el.btnRunDemo.addEventListener('click', toggleDemo);

    // Device refresh
    el.btnRefreshDevices.addEventListener('click', refreshDevices);

    // Remote navigation
    el.btnActionBack.addEventListener('click', () => sendAction('back'));
    el.btnActionHome.addEventListener('click', () => sendAction('home'));
    el.btnActionRecents.addEventListener('click', () => sendAction('recents'));
    el.btnActionLock.addEventListener('click', () => sendAction('lock'));
    el.btnActionWake.addEventListener('click', () => sendAction('wake'));

    // Console
    el.btnToggleConsole.addEventListener('click', () => {
      el.consoleBody.classList.toggle('hidden');
    });

    el.btnCopyLogs.addEventListener('click', () => {
      const text = Array.from(el.consoleLogs.children).map(c => c.textContent).join('\n');
      navigator.clipboard.writeText(text);
      showToast('Logs copied to clipboard', 'success');
    });

    el.btnClearLogs.addEventListener('click', () => {
      el.consoleLogs.innerHTML = '';
      el.logCountBadge.textContent = '0 lines';
      showToast('Console cleared', 'info');
    });

    // Doctor
    el.btnRerunDoctor.addEventListener('click', refreshDoctor);

    el.btnBrowseAdb.addEventListener('click', async () => {
      const res = await api('/adb/browse', { method: 'POST' });
      if (res.ok && res.path) {
        el.inputCustomAdb.value = res.path;
        await refreshDoctor();
        await refreshDevices();
        showToast('Custom ADB path updated!', 'success');
      }
    });

    el.btnSaveAdb.addEventListener('click', async () => {
      await saveCurrentConfig();
      await refreshDoctor();
      await refreshDevices();
      showToast('Custom ADB applied!', 'success');
    });

    // Preferences
    el.btnBrowseRecord.addEventListener('click', async () => {
      const res = await api('/record/browse', { method: 'POST' });
      if (res.ok && res.path) {
        el.inputRecordPath.value = res.path;
        showToast('Recording output path set', 'success');
      }
    });

    el.btnClearRecord.addEventListener('click', () => {
      el.inputRecordPath.value = '';
      saveCurrentConfig();
      showToast('Recording disabled', 'info');
    });

    el.btnSavePrefs.addEventListener('click', async () => {
      await saveCurrentConfig();
      el.prefsSavedMsg.classList.remove('hidden');
      setTimeout(() => el.prefsSavedMsg.classList.add('hidden'), 2500);
      showToast('Preferences saved', 'success');
    });

    // Error Modal controls
    if (el.btnModalClose) {
      el.btnModalClose.addEventListener('click', closeErrorModal);
    }
    if (el.btnModalCloseIcon) {
      el.btnModalCloseIcon.addEventListener('click', closeErrorModal);
    }
    if (el.errorModal) {
      el.errorModal.addEventListener('click', (e) => {
        if (e.target === el.errorModal) closeErrorModal();
      });
    }
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && el.errorModal && !el.errorModal.classList.contains('hidden')) {
        closeErrorModal();
      }
    });
  }

  // --- Initialization ---
  async function init() {
    initTabs();
    initListeners();
    await loadInitialConfig();
    await refreshNetwork();
    await refreshMonitors();
    await refreshDevices();
    await refreshDoctor();

    // Start polling telemetry
    state.pollInterval = setInterval(pollSessionState, 750);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
