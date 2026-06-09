'use strict';

// ------------------------------------------------------------------ SSE

let evtSource = null;

function connectSSE() {
  if (evtSource) evtSource.close();
  evtSource = new EventSource('/events');

  evtSource.onopen = () => {
    setSSEBadge('Connected', 'green');
    loadInitialState();
  };

  evtSource.onmessage = (e) => {
    try {
      const { type, data } = JSON.parse(e.data);
      switch (type) {
        case 'recording_started':
        case 'recording_stopped':
        case 'status_change':
          applyStatus(data);
          break;
        case 'trigger_updated':
          applyStatus(data);
          applyTriggerStatus(data.trigger);
          break;
        case 'usb_inserted':
        case 'usb_removed':
          applyStatus(data.status || {});
          refreshDevices();
          break;
      }
    } catch (_) {}
  };

  evtSource.onerror = () => {
    setSSEBadge('Disconnected', 'red');
    setTimeout(connectSSE, 3000);
  };
}

function setSSEBadge(text, color) {
  const el = document.getElementById('sse-status');
  el.textContent = text;
  el.className = `badge badge-${color}`;
}

// ------------------------------------------------------------------ initial load

async function loadInitialState() {
  try {
    const [status, triggers, devices] = await Promise.all([
      api('/api/recording/status'),
      api('/api/triggers'),
      api('/api/devices'),
    ]);
    applyStatus(status);
    applyTriggerStatus(triggers);
    applyDevices(devices);
  } catch (err) {
    toast('Failed to load initial state: ' + err.message, true);
  }
  // Always load sources config regardless of whether other APIs succeeded.
  loadSourcesConfig();
}

// ------------------------------------------------------------------ recording

async function startRecording() {
  try {
    await api('/api/recording/start', 'POST');
    toast('Recording started');
  } catch (err) {
    toast('Could not start: ' + err.message, true);
  }
}

async function stopRecording() {
  try {
    await api('/api/recording/stop', 'POST');
    toast('Recording stopped');
  } catch (err) {
    toast('Could not stop: ' + err.message, true);
  }
}

// ------------------------------------------------------------------ triggers

async function uploadAudioTrigger(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const res = await apiForm('/api/triggers/audio', fd);
    toast(res.message || 'Audio trigger updated');
    const triggers = await api('/api/triggers');
    applyTriggerStatus(triggers);
  } catch (err) {
    toast('Upload failed: ' + err.message, true);
  }
}

async function uploadFrameTrigger(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const res = await apiForm('/api/triggers/frame', fd);
    toast(res.message || 'Frame trigger updated');
    const triggers = await api('/api/triggers');
    applyTriggerStatus(triggers);
  } catch (err) {
    toast('Upload failed: ' + err.message, true);
  }
}

async function disableTrigger(type) {
  try {
    await api(`/api/triggers/${type}`, 'DELETE');
    toast(`${type} trigger disabled`);
    const triggers = await api('/api/triggers');
    applyTriggerStatus(triggers);
  } catch (err) {
    toast('Error: ' + err.message, true);
  }
}

// ------------------------------------------------------------------ devices

async function refreshDevices() {
  try {
    const devices = await api('/api/devices/refresh', 'POST');
    applyDevices(devices);
    await Promise.all([loadPreviewSources(), loadSourcesConfig()]);
    toast('Devices refreshed');
  } catch (err) {
    toast('Refresh failed: ' + err.message, true);
  }
}

// ------------------------------------------------------------------ DOM updates

function applyStatus(s) {
  const recording = s.is_recording;
  document.getElementById('rec-dot').className   = `dot ${recording ? 'dot-red' : 'dot-gray'}`;
  document.getElementById('rec-label').textContent = recording ? 'Recording' : 'Not Recording';
  document.getElementById('btn-start').disabled = recording;
  document.getElementById('btn-stop').disabled  = !recording;

  const pathEl = document.getElementById('output-path');
  pathEl.textContent = s.output_path ? `Output: ${s.output_path}` : '';

  const usbDevices = s.usb_devices || {};
  const usbKeys = Object.keys(usbDevices);
  const usbEl = document.getElementById('usb-info');
  if (usbKeys.length === 0) {
    usbEl.textContent = 'USB: none';
  } else {
    usbEl.textContent = usbKeys.map(k => {
      const d = usbDevices[k];
      return `USB: ${d.name || k} → ${d.mount_point || '?'}`;
    }).join(' | ');
  }

  const trigEl = document.getElementById('trigger-info');
  trigEl.textContent = s.active_trigger
    ? `Active trigger: ${s.active_trigger}`
    : 'Trigger: none active';
}

function applyTriggerStatus(t) {
  const audio = t && t.audio;
  const audioEl = document.getElementById('audio-trigger-status');
  if (audio && audio.ready) {
    audioEl.textContent =
      `Loaded: ${shortPath(audio.clip_path)} | ${audio.ref_duration}s | threshold ${audio.threshold}`;
  } else {
    audioEl.textContent = 'Not configured';
  }

  const frame = t && t.frame;
  const frameEl = document.getElementById('frame-trigger-status');
  if (frame && frame.ready) {
    frameEl.textContent =
      `Loaded: ${shortPath(frame.image_path)} | threshold ${frame.threshold}`;
  } else {
    frameEl.textContent = 'Not configured';
  }
}

function applyDevices(d) {
  if (!d) return;
  _deviceFramerates = {};
  for (const v of (d.video || [])) {
    if (v.path) _deviceFramerates[v.path] = v.framerates || {};
  }
  const detected = (d.video || []).map(v => {
    const parent = v.parent ? ` [${v.parent}]` : '';
    const gst = v.gst_ready === false ? ' (gst-fail)' : '';
    return `${v.type.toUpperCase()} — ${v.name} (${v.path})${parent}${gst}`;
  });
  if (detected.length) {
    setList('device-video', detected);
  } else if ((d.configured_sources || []).length) {
    setList('device-video', (d.configured_sources || []).map(s =>
      `${(s.type || '?').toUpperCase()} — ${s.id} (${s.device || 'unmapped'}) [configured]`
    ));
  } else {
    setList('device-video', []);
  }
  setList('device-audio-sources',
    (d.audio_sources || []).map(s => s.description || s.name));
  setList('device-audio-sinks',
    (d.audio_sinks || []).map(s => s.description || s.name));
}

function setList(id, items) {
  const ul = document.getElementById(id);
  if (!ul) return;
  if (!items.length) {
    ul.innerHTML = '<li>None detected</li>';
    return;
  }
  ul.innerHTML = items.map(i => `<li>${esc(i)}</li>`).join('');
}

// ------------------------------------------------------------------ helpers

async function api(url, method = 'GET', body = null) {
  const opts = { method };
  if (body !== null) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiForm(url, formData) {
  const res = await fetch(url, { method: 'POST', body: formData });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

let _toastTimer = null;
function toast(msg, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = isError ? '#dc2626' : '#334155';
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function shortPath(p) {
  if (!p) return '—';
  const parts = p.replace(/\\/g, '/').split('/');
  return parts.slice(-2).join('/');
}

// ------------------------------------------------------------------ sources / layout config

let _deviceFramerates = {};  // device_path -> {format: [fps, ...]}
let _canvasSources = [];     // live copy for canvas rendering / drag
let _canvasOverlays = [];    // live copy for canvas rendering / drag
const _sourcesMap = new WeakMap(); // container element → sources array

async function loadSourcesConfig() {
  try {
    const cfg = await api('/api/config');
    renderSourcesConfig(cfg);
  } catch (err) {
    document.getElementById('sources-config-list').innerHTML =
      `<p class="meta">Could not load config: ${esc(err.message)}</p>`;
  }
}

const _FALLBACK_FPS = [60, 30, 25, 24, 15];

function _availableFps(devicePath) {
  const frs = _deviceFramerates[devicePath] || {};
  const all = new Set();
  for (const list of Object.values(frs)) {
    for (const fps of list) all.add(fps);
  }
  // If the device reported nothing (v4l2-ctl unavailable etc.) use common values.
  if (!all.size) _FALLBACK_FPS.forEach(f => all.add(f));
  return [...all].sort((a, b) => b - a);
}

function renderSourcesConfig(cfg) {
  const container = document.getElementById('sources-config-list');
  const sources = cfg.sources || [];

  _canvasSources = sources.map(s => JSON.parse(JSON.stringify(s)));
  _canvasOverlays = (cfg.overlays || []).map(o => JSON.parse(JSON.stringify(o)));

  if (!sources.length) {
    container.innerHTML = '<p class="meta">No sources in config</p>';
  } else {
    container.innerHTML = sources.map((s, idx) => {
      const pos = s.position || {x: 0, y: 0};
      const sz  = s.size    || {width: 0, height: 0};
      const fps  = _availableFps(s.device || '');
      const fpsOpts = fps.length
        ? fps.map(f => `<option value="${f}" ${s.fps === f ? 'selected' : ''}>${f} fps</option>`).join('')
        : `<option value="">${s.fps ? s.fps + ' fps (saved)' : 'auto'}</option>`;
      const fpsSelect = `
        <label>FPS
          <select id="src-fps-${idx}" data-idx="${idx}">
            <option value="" ${!s.fps ? 'selected' : ''}>auto</option>
            ${fpsOpts}
          </select>
        </label>`;

      const curRot = s.rotation || 0;
      const rotSelect = `
        <label>Rotate
          <select id="src-rot-${idx}">
            ${[0,90,180,270].map(r => `<option value="${r}" ${curRot===r?'selected':''}>${r}°</option>`).join('')}
          </select>
        </label>`;
      const curMask = s.mask_shape || 'rect';
      const nPts = (s.mask_points || []).length;
      const mpos = s.mask_position || {};
      const msz  = s.mask_size    || {};
      const showMaskArea = curMask === 'circle' || curMask === 'rect';
      const polyControls = curMask === 'polygon' ? `
        <button type="button" class="btn btn-sm ${_polyEditIdx===idx?'btn-blue':'btn-gray'}"
                onclick="togglePolyEdit(${idx})">${_polyEditIdx===idx?'Done':'Edit points'}</button>
        <button type="button" class="btn btn-sm btn-gray" onclick="clearPolyPoints(${idx})">Clear (${nPts})</button>` : '';
      const maskAreaControls = showMaskArea ? `
        <label>Mask X <input type="number" id="src-mx-${idx}" value="${mpos.x ?? ''}" placeholder="0" min="0" oninput="onSourceInput(${idx})" /></label>
        <label>Mask Y <input type="number" id="src-my-${idx}" value="${mpos.y ?? ''}" placeholder="0" min="0" oninput="onSourceInput(${idx})" /></label>
        <label>Mask W <input type="number" id="src-mw-${idx}" value="${msz.width  ?? ''}" placeholder="full" min="1" oninput="onSourceInput(${idx})" /></label>
        <label>Mask H <input type="number" id="src-mh-${idx}" value="${msz.height ?? ''}" placeholder="full" min="1" oninput="onSourceInput(${idx})" /></label>` : '';
      const maskSelect = `
        <label>Mask
          <select id="src-mask-${idx}" onchange="onSourceInput(${idx})">
            <option value="rect"    ${curMask==='rect'   ?'selected':''}>Rect</option>
            <option value="circle"  ${curMask==='circle' ?'selected':''}>Circle</option>
            <option value="polygon" ${curMask==='polygon'?'selected':''}>Polygon</option>
          </select>
        </label>${polyControls}${maskAreaControls}`;

    return `
      <div class="source-row" data-idx="${idx}">
        <div class="source-row-header">
          ${esc(s.type || '?').toUpperCase()} — ${esc(s.id || '?')}
          <span class="meta" style="font-weight:400;text-transform:none">(${esc(s.device || 'unmapped')})</span>
        </div>
        <div class="source-fields">
          ${fpsSelect}
          ${rotSelect}
          ${maskSelect}
          <label>X <input type="number" id="src-x-${idx}" value="${pos.x}" oninput="onSourceInput(${idx})" /></label>
          <label>Y <input type="number" id="src-y-${idx}" value="${pos.y}" oninput="onSourceInput(${idx})" /></label>
          <label>W <input type="number" id="src-w-${idx}" value="${sz.width}"  min="1" max="3840" oninput="onSourceInput(${idx})" /></label>
          <label>H <input type="number" id="src-h-${idx}" value="${sz.height}" min="1" max="2160" oninput="onSourceInput(${idx})" /></label>
          <label>Z <input type="number" id="src-z-${idx}" value="${s.z_order ?? 0}" min="0" max="99" /></label>
        </div>
      </div>`;
    }).join('');
    _sourcesMap.set(container, cfg.sources);
  }

  // Output fields — only update when caller provides output config
  if (cfg.output !== undefined) {
    const out = cfg.output || {};
    const ow = document.getElementById('out-width');
    const oh = document.getElementById('out-height');
    const of_ = document.getElementById('out-fps');
    if (ow) ow.value = out.width  || 1920;
    if (oh) oh.value = out.height || 1080;
    if (of_) of_.value = out.fps  || 30;
  }

  _sourcesMap.set(container, cfg.sources);

  if (cfg.overlays !== undefined) {
    renderOverlays(cfg.overlays);
  }
  drawCanvas();
}

function onSourceInput(idx) {
  if (!_canvasSources[idx]) return;
  const x = parseInt(document.getElementById(`src-x-${idx}`)?.value || 0);
  const y = parseInt(document.getElementById(`src-y-${idx}`)?.value || 0);
  const w = parseInt(document.getElementById(`src-w-${idx}`)?.value || 0);
  const h = parseInt(document.getElementById(`src-h-${idx}`)?.value || 0);
  _canvasSources[idx].position = { x, y };
  _canvasSources[idx].size = { width: w, height: h };
  const mxRaw = document.getElementById(`src-mx-${idx}`)?.value;
  const myRaw = document.getElementById(`src-my-${idx}`)?.value;
  const mwRaw = document.getElementById(`src-mw-${idx}`)?.value;
  const mhRaw = document.getElementById(`src-mh-${idx}`)?.value;
  if (mxRaw !== undefined) {
    _canvasSources[idx].mask_position = { x: parseInt(mxRaw) || 0, y: parseInt(myRaw) || 0 };
    _canvasSources[idx].mask_size = { width: parseInt(mwRaw) || 0, height: parseInt(mhRaw) || 0 };
  }
  const newMask = document.getElementById(`src-mask-${idx}`)?.value || 'rect';
  const prevMask = _canvasSources[idx].mask_shape;
  _canvasSources[idx].mask_shape = newMask;
  // Switching away from polygon exits edit mode
  if (prevMask === 'polygon' && newMask !== 'polygon') {
    _polyEditIdx = null;
  }
  // Re-render controls so poly buttons appear/disappear
  if (newMask !== prevMask) {
    renderSourcesConfig({ sources: _canvasSources });
  }
  drawCanvas();
}

async function applySourcesConfig() {
  const container = document.getElementById('sources-config-list');
  const sources = _sourcesMap.get(container) || [];

  const patches = sources.map((s, idx) => {
    const fpsVal = document.getElementById(`src-fps-${idx}`)?.value;
    const rotVal = document.getElementById(`src-rot-${idx}`)?.value;
    const maskShape = document.getElementById(`src-mask-${idx}`)?.value || 'rect';
    return {
      id: s.id,
      fps:         fpsVal ? parseInt(fpsVal) : null,
      rotation:    rotVal ? parseInt(rotVal) : 0,
      mask_shape:    maskShape,
      mask_points:   maskShape === 'polygon' ? (_canvasSources[idx]?.mask_points ?? []) : [],
      mask_position: _canvasSources[idx]?.mask_position || null,
      mask_size:     _canvasSources[idx]?.mask_size || null,
      position: {
        x: parseInt(document.getElementById(`src-x-${idx}`)?.value || 0),
        y: parseInt(document.getElementById(`src-y-${idx}`)?.value || 0),
      },
      size: {
        width:  parseInt(document.getElementById(`src-w-${idx}`)?.value || 0),
        height: parseInt(document.getElementById(`src-h-${idx}`)?.value || 0),
      },
      z_order: parseInt(document.getElementById(`src-z-${idx}`)?.value || 0),
    };
  });

  const output = {
    width:  parseInt(document.getElementById('out-width')?.value  || 1920),
    height: parseInt(document.getElementById('out-height')?.value || 1080),
    fps:    parseInt(document.getElementById('out-fps')?.value    || 30),
  };

  try {
    await api('/api/config', 'POST', { sources: patches, output });
    toast('Config saved — preview restarting');
    setTimeout(loadSourcesConfig, 1200);
  } catch (err) {
    toast('Save failed: ' + err.message, true);
  }
}

// ------------------------------------------------------------------ canvas layout editor

const _CANVAS_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
let _dragState = null;   // { type:'source'|'overlay', idx, startX, startY, origX, origY, scale }
let _polyEditIdx = null; // source index currently being polygon-edited

function _canvasScale() {
  const canvas = document.getElementById('layout-canvas');
  if (!canvas) return 1;
  const outW = parseInt(document.getElementById('out-width')?.value || 1920);
  const outH = parseInt(document.getElementById('out-height')?.value || 1080);
  return Math.min(canvas.width / outW, canvas.height / outH);
}

function drawCanvas() {
  const canvas = document.getElementById('layout-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const outW = parseInt(document.getElementById('out-width')?.value || 1920);
  const outH = parseInt(document.getElementById('out-height')?.value || 1080);
  const scale = Math.min(canvas.width / outW, canvas.height / outH);

  const bgW = Math.round(outW * scale);
  const bgH = Math.round(outH * scale);

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Output frame background
  ctx.fillStyle = '#0a0c14';
  ctx.fillRect(0, 0, bgW, bgH);
  ctx.strokeStyle = '#2d3148';
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, bgW - 1, bgH - 1);

  // Draw sources
  _canvasSources.forEach((s, i) => {
    const pos = s.position || { x: 0, y: 0 };
    const sz  = s.size    || { width: 200, height: 200 };
    const x = Math.round(pos.x * scale);
    const y = Math.round(pos.y * scale);
    const w = Math.max(1, Math.round(sz.width  * scale));
    const h = Math.max(1, Math.round(sz.height * scale));
    const color = _CANVAS_COLORS[i % _CANVAS_COLORS.length];
    const maskShape = s.mask_shape || 'rect';
    const pts = s.mask_points || [];

    ctx.save();
    ctx.fillStyle = color + '28';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;

    const mpos = s.mask_position || {};
    const msz  = s.mask_size    || {};
    const hasMaskArea = msz.width > 0 && msz.height > 0;
    // mx/my/mw/mh: mask area in canvas pixels, relative to compositor origin
    const mx = x + (mpos.x || 0) * scale;
    const my = y + (mpos.y || 0) * scale;
    const mw = hasMaskArea ? msz.width  * scale : w;
    const mh = hasMaskArea ? msz.height * scale : h;

    if (maskShape === 'circle') {
      // Draw source bounding box dimmed, then circle bright
      if (hasMaskArea) {
        ctx.globalAlpha = 0.15;
        ctx.fillRect(x, y, w, h);
        ctx.globalAlpha = 1;
        ctx.setLineDash([4, 3]);
        ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
        ctx.setLineDash([]);
      }
      const cx = mx + mw / 2, cy = my + mh / 2, r = Math.min(mw, mh) / 2;
      ctx.fillStyle = color + '50';
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, 2 * Math.PI);
      ctx.fill();
      ctx.stroke();
    } else if (maskShape === 'rect' && hasMaskArea) {
      // Draw source bounding box dimmed, then mask rect bright
      ctx.globalAlpha = 0.15;
      ctx.fillRect(x, y, w, h);
      ctx.globalAlpha = 1;
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
      ctx.setLineDash([]);
      ctx.fillStyle = color + '50';
      ctx.fillRect(mx, my, mw, mh);
      ctx.strokeRect(mx + 1, my + 1, mw - 2, mh - 2);
    } else if (maskShape === 'polygon' && pts.length >= 3) {
      ctx.beginPath();
      ctx.moveTo(x + pts[0][0] * w, y + pts[0][1] * h);
      for (let j = 1; j < pts.length; j++) ctx.lineTo(x + pts[j][0] * w, y + pts[j][1] * h);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    } else {
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
    }

    // Polygon edit mode: draw vertex handles + dashed outline
    if (_polyEditIdx === i) {
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      pts.forEach(([px, py]) => {
        ctx.beginPath();
        ctx.arc(x + px * w, y + py * h, 4, 0, 2 * Math.PI);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      });
    }

    ctx.fillStyle = '#fff';
    ctx.font = 'bold 12px system-ui';
    ctx.shadowColor = '#000';
    ctx.shadowBlur = 3;
    const rot = s.rotation || 0;
    const label = rot ? `${s.id || '?'} ↻${rot}°` : (s.id || '?');
    ctx.fillText(label, x + 6, y + 16);
    ctx.shadowBlur = 0;
    ctx.restore();
  });

  // Draw overlays
  _canvasOverlays.forEach((o, i) => {
    const pos = o.position || { x: 0, y: 0 };
    const sz  = o.size    || { width: 100, height: 100 };
    const x = Math.round(pos.x * scale);
    const y = Math.round(pos.y * scale);
    const w = Math.max(1, Math.round(sz.width  * scale));
    const h = Math.max(1, Math.round(sz.height * scale));

    ctx.fillStyle = 'rgba(255,255,255,0.12)';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
    ctx.setLineDash([]);

    ctx.fillStyle = '#e2e8f0';
    ctx.font = '11px system-ui';
    ctx.fillText(`OVL ${i}`, x + 4, y + 14);
  });
}

function _initCanvas() {
  const canvas = document.getElementById('layout-canvas');
  if (!canvas) return;

  canvas.addEventListener('mousedown', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const scale = _canvasScale();

    // Polygon edit mode: add/remove vertex on click
    if (_polyEditIdx !== null) {
      const s = _canvasSources[_polyEditIdx];
      if (s) {
        const pos = s.position || { x: 0, y: 0 };
        const sz  = s.size    || { width: 200, height: 200 };
        const sx = pos.x * scale, sy = pos.y * scale;
        const sw = sz.width * scale, sh = sz.height * scale;
        if (!s.mask_points) s.mask_points = [];
        const pts = s.mask_points;
        const HIT = 8;
        // Click near existing vertex → remove it
        const rIdx = pts.findIndex(([px, py]) =>
          Math.abs(mx - (sx + px * sw)) < HIT && Math.abs(my - (sy + py * sh)) < HIT
        );
        if (rIdx >= 0) {
          pts.splice(rIdx, 1);
        } else if (_hitTest(mx, my, sx, sy, sw, sh)) {
          // Click inside source → add vertex (normalized to source)
          pts.push([
            Math.round(((mx - sx) / sw) * 1000) / 1000,
            Math.round(((my - sy) / sh) * 1000) / 1000,
          ]);
        }
        drawCanvas();
      }
      return;
    }

    // Check overlays first (higher z) then sources
    for (let i = _canvasOverlays.length - 1; i >= 0; i--) {
      const o = _canvasOverlays[i];
      const pos = o.position || { x: 0, y: 0 };
      const sz  = o.size    || { width: 100, height: 100 };
      if (_hitTest(mx, my, pos.x * scale, pos.y * scale, sz.width * scale, sz.height * scale)) {
        _dragState = { type: 'overlay', idx: i, startX: mx, startY: my, origX: pos.x, origY: pos.y, scale };
        canvas.style.cursor = 'grabbing';
        return;
      }
    }
    for (let i = _canvasSources.length - 1; i >= 0; i--) {
      const s = _canvasSources[i];
      const pos = s.position || { x: 0, y: 0 };
      const sz  = s.size    || { width: 200, height: 200 };
      if (_hitTest(mx, my, pos.x * scale, pos.y * scale, sz.width * scale, sz.height * scale)) {
        _dragState = { type: 'source', idx: i, startX: mx, startY: my, origX: pos.x, origY: pos.y, scale };
        canvas.style.cursor = 'grabbing';
        return;
      }
    }
  });

  canvas.addEventListener('mousemove', (e) => {
    if (!_dragState) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const { idx, startX, startY, origX, origY, scale, type } = _dragState;

    const newX = Math.round(origX + (mx - startX) / scale);
    const newY = Math.round(origY + (my - startY) / scale);

    if (type === 'source' && _canvasSources[idx]) {
      _canvasSources[idx].position = { ..._canvasSources[idx].position, x: newX, y: newY };
      const xEl = document.getElementById(`src-x-${idx}`);
      const yEl = document.getElementById(`src-y-${idx}`);
      if (xEl) xEl.value = newX;
      if (yEl) yEl.value = newY;
    } else if (type === 'overlay' && _canvasOverlays[idx]) {
      _canvasOverlays[idx].position = { ..._canvasOverlays[idx].position, x: newX, y: newY };
      const xEl = document.getElementById(`ovl-x-${idx}`);
      const yEl = document.getElementById(`ovl-y-${idx}`);
      if (xEl) xEl.value = newX;
      if (yEl) yEl.value = newY;
    }
    drawCanvas();
  });

  const stopDrag = () => {
    _dragState = null;
    canvas.style.cursor = 'default';
  };
  canvas.addEventListener('mouseup', stopDrag);
  canvas.addEventListener('mouseleave', stopDrag);
}

function _hitTest(mx, my, x, y, w, h) {
  return mx >= x && mx <= x + w && my >= y && my <= y + h;
}

function togglePolyEdit(idx) {
  _polyEditIdx = (_polyEditIdx === idx) ? null : idx;
  renderSourcesConfig({ sources: _canvasSources });
  drawCanvas();
}

function clearPolyPoints(idx) {
  if (_canvasSources[idx]) {
    _canvasSources[idx].mask_points = [];
    renderSourcesConfig({ sources: _canvasSources });
    drawCanvas();
  }
}

// ------------------------------------------------------------------ overlays

function renderOverlays(overlays) {
  const el = document.getElementById('overlays-list');
  if (!el) return;
  if (!overlays.length) {
    el.innerHTML = '<p class="meta">No overlays configured</p>';
    return;
  }
  el.innerHTML = overlays.map((o, i) => {
    const pos = o.position || {x:0,y:0};
    const sz  = o.size    || {width:200,height:200};
    const imgSrc = o.image_url ? `<img class="overlay-thumb" src="${esc(o.image_url)}" alt="overlay ${i}" />` : '';
    return `
    <div class="overlay-item" data-idx="${i}">
      ${imgSrc}
      <div class="overlay-fields">
        <label>X <input type="number" id="ovl-x-${i}" value="${pos.x}" min="0" oninput="onOverlayInput(${i})" /></label>
        <label>Y <input type="number" id="ovl-y-${i}" value="${pos.y}" min="0" oninput="onOverlayInput(${i})" /></label>
        <label>W <input type="number" id="ovl-w-${i}" value="${sz.width}" min="1" oninput="onOverlayInput(${i})" /></label>
        <label>H <input type="number" id="ovl-h-${i}" value="${sz.height}" min="1" oninput="onOverlayInput(${i})" /></label>
        <label>Opacity <input type="number" id="ovl-op-${i}" value="${o.opacity ?? 1}" min="0" max="1" step="0.05" /></label>
        <label>Z <input type="number" id="ovl-z-${i}" value="${o.z_order ?? 100+i}" min="0" /></label>
      </div>
      <div class="actions" style="margin-top:8px">
        <button class="btn btn-blue btn-sm" onclick="applyOverlay(${i})">Apply</button>
        <button class="btn btn-red btn-sm" onclick="deleteOverlay(${i})">Remove</button>
      </div>
    </div>`;
  }).join('');
}

function onOverlayInput(i) {
  if (!_canvasOverlays[i]) return;
  const x = parseInt(document.getElementById(`ovl-x-${i}`)?.value || 0);
  const y = parseInt(document.getElementById(`ovl-y-${i}`)?.value || 0);
  const w = parseInt(document.getElementById(`ovl-w-${i}`)?.value || 0);
  const h = parseInt(document.getElementById(`ovl-h-${i}`)?.value || 0);
  _canvasOverlays[i].position = { x, y };
  _canvasOverlays[i].size = { width: w, height: h };
  drawCanvas();
}

async function applyOverlay(idx) {
  const patch = {
    position: {
      x: parseInt(document.getElementById(`ovl-x-${idx}`)?.value || 0),
      y: parseInt(document.getElementById(`ovl-y-${idx}`)?.value || 0),
    },
    size: {
      width:  parseInt(document.getElementById(`ovl-w-${idx}`)?.value || 200),
      height: parseInt(document.getElementById(`ovl-h-${idx}`)?.value || 200),
    },
    opacity: parseFloat(document.getElementById(`ovl-op-${idx}`)?.value || 1),
    z_order: parseInt(document.getElementById(`ovl-z-${idx}`)?.value || 100),
  };
  try {
    await api(`/api/config/overlays/${idx}`, 'PATCH', patch);
    toast('Overlay updated');
    loadSourcesConfig();
  } catch (err) {
    toast('Update failed: ' + err.message, true);
  }
}

async function deleteOverlay(idx) {
  try {
    await api(`/api/config/overlays/${idx}`, 'DELETE');
    toast('Overlay removed');
    loadSourcesConfig();
  } catch (err) {
    toast('Remove failed: ' + err.message, true);
  }
}

async function uploadOverlay(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    await apiForm('/api/config/overlays', fd);
    toast('Overlay added');
    e.target.reset();
    loadSourcesConfig();
  } catch (err) {
    toast('Upload failed: ' + err.message, true);
  }
}

// ------------------------------------------------------------------ WebSocket preview

let _previewSources = [];
let _previewPollInterval = null;
const _activeWs = new Map(); // key → WebSocket, for cleanup on re-render

/**
 * Stream binary JPEG frames from a WebSocket into a <canvas>.
 * Uses createImageBitmap (off main thread) and drops frames when the decoder
 * is busy, so latency never accumulates. Auto-reconnects on close.
 * Returns a stop function.
 */
function initPreviewWs(canvas, wsUrl, placeholder) {
  let ws = null;
  let decoding = false;
  let stopped = false;
  const ctx = canvas.getContext('2d');

  function connect() {
    if (stopped) return;
    ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    ws.onmessage = async (e) => {
      if (!e.data.byteLength || decoding) return;
      decoding = true;
      try {
        const blob = new Blob([e.data], { type: 'image/jpeg' });
        const bmp  = await createImageBitmap(blob);
        ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
        bmp.close();
        if (placeholder) placeholder.classList.add('hidden');
        canvas.classList.add('visible');
      } catch (_) {}
      decoding = false;
    };
    ws.onclose = () => { if (!stopped) setTimeout(connect, 2000); };
    ws.onerror = () => ws.close();
  }

  connect();
  return () => { stopped = true; if (ws) ws.close(); };
}

function _wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${path}`;
}

function renderCameraGrid(sources) {
  const grid = document.getElementById('cameras-grid');
  if (!grid) return;

  // Stop existing per-source WebSockets before re-rendering
  _activeWs.forEach(stop => stop());
  _activeWs.clear();

  if (!sources.length) {
    grid.innerHTML = '<p class="meta">No video sources configured</p>';
    return;
  }
  grid.innerHTML = sources.map(s => {
    const sizeHint   = s.capture_size ? ` @ ${esc(s.capture_size)}` : '';
    const signalHint = s.type === 'hdmi' && s.hdmi_signal === false
      ? ' <span class="meta">— no HDMI signal</span>' : '';
    const runHint = s.preview_running === false
      ? ' <span class="meta">— pipeline off</span>' : '';
    return `
    <div class="camera-card">
      <h3>${esc(s.type.toUpperCase())} — ${esc(s.id)} <span class="meta">(${esc(s.device)}${sizeHint})</span>${signalHint}${runHint}</h3>
      <div class="camera-wrap">
        <canvas id="cam-cv-${s.id}" width="320" height="180"></canvas>
        <div id="cam-ph-${s.id}" class="camera-placeholder">Waiting for ${esc(s.id)}…</div>
      </div>
    </div>
  `}).join('');

  for (const s of sources) {
    const cv = document.getElementById('cam-cv-' + s.id);
    const ph = document.getElementById('cam-ph-' + s.id);
    if (cv) {
      const stop = initPreviewWs(cv, _wsUrl(`/api/preview/source/${encodeURIComponent(s.id)}/ws`), ph);
      _activeWs.set(s.id, stop);
    }
  }
}

async function loadPreviewSources() {
  try {
    const data = await api('/api/preview/sources');
    const sources = data.sources || [];
    const key = sources.map(s =>
      [s.id, s.device, s.preview_running, s.has_frame].join(':')
    ).join(',');
    const prevKey = _previewSources.map(s =>
      [s.id, s.device, s.preview_running, s.has_frame].join(':')
    ).join(',');
    if (key !== prevKey) {
      _previewSources = sources;
      renderCameraGrid(sources);
    }
  } catch (_) {
    const grid = document.getElementById('cameras-grid');
    if (grid) grid.innerHTML = '<p class="meta">Could not load preview sources</p>';
  }
}

function initPreview() {
  const canvas = document.getElementById('preview-canvas');
  const ph     = document.getElementById('preview-placeholder');
  if (canvas) initPreviewWs(canvas, _wsUrl('/api/preview/ws'), ph);

  _previewPollInterval = setInterval(loadPreviewSources, 5000);
  loadPreviewSources();
}

// ------------------------------------------------------------------ init

_initCanvas();
loadSourcesConfig();
connectSSE();
initPreview();
