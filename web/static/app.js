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
  // audio
  const audio = t && t.audio;
  const audioEl = document.getElementById('audio-trigger-status');
  if (audio && audio.ready) {
    audioEl.textContent =
      `Loaded: ${shortPath(audio.clip_path)} | ${audio.ref_duration}s | threshold ${audio.threshold}`;
  } else {
    audioEl.textContent = 'Not configured';
  }

  // frame
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
  setList('device-video',
    (d.video || []).map(v => `${v.type.toUpperCase()} — ${v.name} (${v.path})`));
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

async function api(url, method = 'GET') {
  const res = await fetch(url, { method });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
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

// ------------------------------------------------------------------ init

connectSSE();
