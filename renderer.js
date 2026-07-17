const els = {
  modeCapture: document.getElementById('modeCapture'),
  modeRecord: document.getElementById('modeRecord'),
  connUsb: document.getElementById('connUsb'),
  connWifi: document.getElementById('connWifi'),
  wifiAddress: document.getElementById('wifiAddress'),
  wifiShell: document.getElementById('wifiShell'),
  wifiHint: document.getElementById('wifiHint'),
  wifiPortHint: document.getElementById('wifiPortHint'),
  btnClearWifi: document.getElementById('btnClearWifi'),
  btnConnectWifi: document.getElementById('btnConnectWifi'),
  bitrate: document.getElementById('bitrate'),
  maxFps: document.getElementById('maxFps'),
  maxSize: document.getElementById('maxSize'),
  format: document.getElementById('format'),
  useObsAudio: document.getElementById('useObsAudio'),
  stayAwake: document.getElementById('stayAwake'),
  turnScreenOff: document.getElementById('turnScreenOff'),
  recordDir: document.getElementById('recordDir'),
  filePath: document.getElementById('filePath'),
  btnFolder: document.getElementById('btnFolder'),
  btnStart: document.getElementById('btnStart'),
  btnStartLabel: document.getElementById('btnStartLabel'),
  btnStop: document.getElementById('btnStop'),
  statusPill: document.getElementById('statusPill'),
  statusText: document.getElementById('statusText'),
  hintText: document.getElementById('hintText'),
  connStatus: document.getElementById('connStatus'),
  remoteUrl: document.getElementById('remoteUrl'),
  remoteQr: document.getElementById('remoteQr'),
  btnCopyRemote: document.getElementById('btnCopyRemote'),
};

let mode = 'record';
let connection = 'usb';
let isActive = false;

const IP_HOST_RE = /^(?:\d{1,3}\.){3}\d{1,3}$/;

function normalizeWifiInput(raw) {
  return String(raw || '')
    .trim()
    .replace(/\s+/g, '')
    .replace(/,/g, '.')
    .replace(/[^0-9.:]/g, '');
}

function parseWifiAddress(raw) {
  const value = normalizeWifiInput(raw);
  if (!value) return { empty: true, valid: true, host: '', display: '', port: '5555', full: '' };

  const [host, portPart] = value.split(':');
  const octets = host.split('.');
  const octetsOk = octets.length === 4 && octets.every((o) => {
    if (!/^\d{1,3}$/.test(o)) return false;
    const n = Number(o);
    return n >= 0 && n <= 255;
  }) && IP_HOST_RE.test(host);

  let port = '5555';
  let portOk = true;
  if (portPart != null && portPart !== '') {
    portOk = /^\d{1,5}$/.test(portPart) && Number(portPart) >= 1 && Number(portPart) <= 65535;
    port = portPart;
  }

  const isValid = octetsOk && portOk;
  return {
    empty: false,
    valid: isValid,
    host,
    port,
    display: host,
    full: isValid ? `${host}:${port}` : value,
  };
}

function updateWifiFieldUI() {
  if (!els.wifiAddress) return;
  const parsed = parseWifiAddress(els.wifiAddress.value);
  const hasText = !!els.wifiAddress.value.trim();

  if (els.btnClearWifi) els.btnClearWifi.hidden = !hasText;
  if (els.wifiPortHint) {
    els.wifiPortHint.style.opacity = parsed.empty || (hasText && els.wifiAddress.value.includes(':')) ? '0.35' : '1';
  }

  if (!els.wifiShell || !els.wifiHint) return;

  els.wifiShell.classList.remove('is-invalid', 'is-valid');
  els.wifiHint.classList.remove('error', 'ok');
  els.wifiAddress.setAttribute('aria-invalid', 'false');

  if (parsed.empty) {
    els.wifiHint.textContent = 'Digite só o IP. A porta 5555 é adicionada automaticamente.';
    if (els.btnConnectWifi) els.btnConnectWifi.disabled = false; // ainda pode detectar via USB
    return;
  }

  if (!parsed.valid) {
    els.wifiShell.classList.add('is-invalid');
    els.wifiHint.classList.add('error');
    els.wifiAddress.setAttribute('aria-invalid', 'true');
    els.wifiHint.textContent = 'IP inválido. Use o formato 192.168.0.20';
    if (els.btnConnectWifi) els.btnConnectWifi.disabled = true;
    return;
  }

  els.wifiShell.classList.add('is-valid');
  els.wifiHint.classList.add('ok');
  els.wifiHint.textContent = `Pronto · conectará em ${parsed.full}`;
  if (els.btnConnectWifi) els.btnConnectWifi.disabled = false;
}

function getWifiAddressForApi() {
  const parsed = parseWifiAddress(els.wifiAddress.value);
  if (parsed.empty) return '';
  if (!parsed.valid) return els.wifiAddress.value.trim();
  return parsed.host; // main adiciona :5555
}

function getSettings() {
  return {
    mode,
    connection,
    wifiAddress: getWifiAddressForApi(),
    bitrate: els.bitrate.value,
    maxFps: els.maxFps.value,
    maxSize: els.maxSize.value,
    format: els.format.value,
    useObsAudio: els.useObsAudio.checked,
    stayAwake: els.stayAwake.checked,
    turnScreenOff: els.turnScreenOff.checked,
  };
}

function applyModeUI() {
  const isRecord = mode === 'record';
  els.modeCapture.classList.toggle('active', !isRecord);
  els.modeRecord.classList.toggle('active', isRecord);

  document.querySelectorAll('.record-only').forEach((el) => {
    el.classList.toggle('hidden', !isRecord);
  });

  els.btnStartLabel.textContent = isRecord ? 'Iniciar gravação' : 'Iniciar captura';
  els.hintText.textContent = isRecord
    ? `Gravar via ${connection.toUpperCase()}: vídeo scrcpy + áudio OBS → sync`
    : `Capturar via ${connection.toUpperCase()}: espelha a tela sem salvar arquivo`;

  if (!isActive) {
    els.statusText.textContent = isRecord ? 'Pronto para gravar' : 'Pronto para capturar';
  }
}

function applyConnectionUI() {
  const isWifi = connection === 'wifi';
  els.connUsb.classList.toggle('active', !isWifi);
  els.connWifi.classList.toggle('active', isWifi);
  document.querySelectorAll('.wifi-only').forEach((el) => {
    el.classList.toggle('hidden', !isWifi);
  });
  applyModeUI();
}

function setControlsEnabled(enabled) {
  [els.modeCapture, els.modeRecord, els.connUsb, els.connWifi, els.wifiAddress,
    els.btnClearWifi, els.btnConnectWifi,
    els.bitrate, els.maxFps, els.maxSize, els.format,
    els.useObsAudio, els.stayAwake, els.turnScreenOff, els.btnFolder].forEach((el) => {
    if (el) el.disabled = !enabled;
  });
}

function setActiveState(active, info = {}) {
  isActive = active;
  setControlsEnabled(!active);

  if (active) {
    els.statusPill.classList.add('active');
    els.statusText.textContent = mode === 'record' ? 'GRAVANDO' : 'CAPTURANDO';
    els.btnStart.style.display = 'none';
    els.btnStop.classList.add('visible');
    els.btnStop.disabled = false;
    if (info.videoPath) {
      els.filePath.textContent = info.videoPath;
    } else if (mode === 'capture') {
      els.filePath.textContent = 'Espelhamento ativo (sem arquivo)';
    }
  } else {
    els.statusPill.classList.remove('active');
    els.btnStart.style.display = 'flex';
    els.btnStart.disabled = false;
    els.btnStop.classList.remove('visible');
    applyModeUI();
  }
}

els.modeCapture.addEventListener('click', () => {
  if (isActive) return;
  mode = 'capture';
  applyModeUI();
  window.api.saveSettings(getSettings());
});

els.modeRecord.addEventListener('click', () => {
  if (isActive) return;
  mode = 'record';
  applyModeUI();
  window.api.saveSettings(getSettings());
});

async function switchConnection(next) {
  if (isActive) return;

  if (next === 'wifi') {
    const parsed = parseWifiAddress(els.wifiAddress.value);
    if (!parsed.empty && !parsed.valid) {
      updateWifiFieldUI();
      els.wifiAddress.focus();
      return;
    }
  }

  connection = next;
  applyConnectionUI();

  if (els.connStatus) {
    els.connStatus.textContent = next === 'wifi'
      ? 'Ativando ADB Wi‑Fi (tcpip + connect)…'
      : 'Ativando depuração USB (adb usb)…';
  }

  const result = await window.api.activateConnection({
    connection: next,
    wifiAddress: getWifiAddressForApi(),
  });

  window.api.saveSettings(getSettings());

  if (result.wifiAddress && els.wifiAddress) {
    els.wifiAddress.value = String(result.wifiAddress).replace(/:5555$/, '');
    updateWifiFieldUI();
  }

  if (els.connStatus) {
    els.connStatus.textContent = result.message || (result.success ? 'OK' : 'Falha');
  }

  if (!result.success) {
    alert(result.message);
  }
}

els.connUsb.addEventListener('click', () => switchConnection('usb'));
els.connWifi.addEventListener('click', () => switchConnection('wifi'));

if (els.wifiAddress) {
  els.wifiAddress.addEventListener('input', () => {
    const caret = els.wifiAddress.selectionStart;
    const before = els.wifiAddress.value;
    const cleaned = normalizeWifiInput(before);
    if (cleaned !== before) {
      els.wifiAddress.value = cleaned;
      const nextCaret = Math.max(0, (caret || 0) - (before.length - cleaned.length));
      try { els.wifiAddress.setSelectionRange(nextCaret, nextCaret); } catch (_) {}
    }
    updateWifiFieldUI();
  });

  els.wifiAddress.addEventListener('paste', (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData('text');
    const cleaned = normalizeWifiInput(text);
    document.execCommand('insertText', false, cleaned);
    updateWifiFieldUI();
  });

  els.wifiAddress.addEventListener('blur', () => {
    const parsed = parseWifiAddress(els.wifiAddress.value);
    if (parsed.valid && !parsed.empty) {
      els.wifiAddress.value = parsed.host;
    }
    updateWifiFieldUI();
    window.api.saveSettings(getSettings());
  });

  els.wifiAddress.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (els.btnConnectWifi && !els.btnConnectWifi.disabled) {
        els.btnConnectWifi.click();
      }
    }
  });
}

if (els.btnClearWifi) {
  els.btnClearWifi.addEventListener('click', () => {
    els.wifiAddress.value = '';
    updateWifiFieldUI();
    els.wifiAddress.focus();
    window.api.saveSettings(getSettings());
  });
}

if (els.btnConnectWifi) {
  els.btnConnectWifi.addEventListener('click', async () => {
    const parsed = parseWifiAddress(els.wifiAddress.value);
    if (!parsed.empty && !parsed.valid) {
      updateWifiFieldUI();
      els.wifiAddress.focus();
      return;
    }
    await switchConnection('wifi');
  });
}

['bitrate', 'maxFps', 'maxSize', 'format', 'useObsAudio', 'stayAwake', 'turnScreenOff'].forEach((id) => {
  els[id].addEventListener('change', () => window.api.saveSettings(getSettings()));
});

els.btnFolder.addEventListener('click', async () => {
  const result = await window.api.chooseFolder();
  if (result) {
    els.recordDir.value = result.dir;
    els.filePath.textContent = 'Próximo: ' + result.preview;
  }
});

els.btnStart.addEventListener('click', async () => {
  els.btnStart.disabled = true;
  els.btnStartLabel.textContent = 'Iniciando...';

  const result = await window.api.startRecording(getSettings());

  if (result.success) {
    setActiveState(true, result);
  } else {
    alert(result.message);
    els.btnStart.disabled = false;
    applyModeUI();
  }
});

els.btnStop.addEventListener('click', async () => {
  els.btnStop.disabled = true;
  els.btnStop.textContent = 'Parando...';

  const result = await window.api.stopRecording();

  if (result.success) {
    setActiveState(false);
    if (result.videoPath) {
      els.filePath.textContent = 'Salvo: ' + result.videoPath;
    } else {
      els.filePath.textContent = result.message || 'Captura encerrada';
    }
  } else {
    alert(result.message);
  }

  els.btnStop.disabled = false;
  els.btnStop.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
    Parar
  `;
});

window.api.onRecordingStarted((data) => {
  if (data.mode) mode = data.mode;
  setActiveState(true, data);
});

window.api.onRecordingStopped(() => {
  setActiveState(false);
});

window.api.onRecordingError((msg) => {
  alert('Erro: ' + msg);
  setActiveState(false);
});

els.btnCopyRemote.addEventListener('click', async () => {
  const url = els.remoteUrl.textContent;
  if (!url || url.includes('…') || url.includes('falhou')) return;
  try {
    await navigator.clipboard.writeText(url);
    els.btnCopyRemote.textContent = 'Copiado!';
    setTimeout(() => { els.btnCopyRemote.textContent = 'Copiar link'; }, 1500);
  } catch (_) {
    els.btnCopyRemote.textContent = 'Selecione o link';
  }
});

function showRemoteInfo(info) {
  const url = info?.primaryUrl || (info?.urls && info.urls[0]);
  if (!url) {
    els.remoteUrl.textContent = 'Servidor remoto indisponível';
    els.remoteQr.removeAttribute('src');
    return;
  }
  els.remoteUrl.textContent = url;
  els.remoteQr.src = 'https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=' + encodeURIComponent(url);
}

window.api.getStatus().then((status) => {
  if (status.recordDir) els.recordDir.value = status.recordDir;
  if (status.settings) {
    const s = status.settings;
    mode = s.mode || 'record';
    connection = s.connection || 'usb';
    if (s.wifiAddress) els.wifiAddress.value = s.wifiAddress;
    if (s.bitrate) els.bitrate.value = s.bitrate;
    if (s.maxFps) els.maxFps.value = s.maxFps;
    if (s.maxSize) els.maxSize.value = s.maxSize;
    if (s.format) els.format.value = s.format;
    els.useObsAudio.checked = s.useObsAudio !== false;
    els.stayAwake.checked = s.stayAwake !== false;
    els.turnScreenOff.checked = !!s.turnScreenOff;
  }
  applyConnectionUI();
  updateWifiFieldUI();
  if (status.isRecording) setActiveState(true, { videoPath: status.videoPath });
  if (status.remote) showRemoteInfo(status.remote);
});

setTimeout(() => {
  window.api.getRemoteInfo().then(showRemoteInfo).catch(() => {});
}, 800);
