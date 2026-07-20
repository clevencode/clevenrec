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
  audioSource: document.getElementById('audioSource'),
  stayAwake: document.getElementById('stayAwake'),
  turnScreenOff: document.getElementById('turnScreenOff'),
  audioOff: document.getElementById('audioOff'),
  audioPc: document.getElementById('audioPc'),
  audioPhone: document.getElementById('audioPhone'),
  audioPcDesktop: document.getElementById('audioPcDesktop'),
  audioPcMic: document.getElementById('audioPcMic'),
  audioPcBoth: document.getElementById('audioPcBoth'),
  pcAudioField: document.getElementById('pcAudioField'),
  audioHint: document.getElementById('audioHint'),
  modeHint: document.getElementById('modeHint'),
  optionsHint: document.getElementById('optionsHint'),
  screenKeep: document.getElementById('screenKeep'),
  screenDefault: document.getElementById('screenDefault'),
  screenOff: document.getElementById('screenOff'),
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
  qualityTitle: document.getElementById('qualityTitle'),
  qualityHint: document.getElementById('qualityHint'),
  remoteUrl: document.getElementById('remoteUrl'),
  remoteQr: document.getElementById('remoteQr'),
  btnCopyRemote: document.getElementById('btnCopyRemote'),
};

let mode = 'record';
let connection = 'usb';
let screenBehavior = 'keep'; // keep | default | off
let isActive = false;
let isConnecting = false;
let sessionHadAudio = false;
let sessionAudioSource = 'none';

const IP_HOST_RE = /^(?:\d{1,3}\.){3}\d{1,3}$/;

const SCREEN_HINTS = {
  keep: 'Mantém a tela acesa durante a sessão.',
  default: 'O celular segue o tempo de tela padrão.',
  off: 'Apaga a tela do celular; a imagem continua no PC.',
};

const AUDIO_HINTS = {
  none: 'Sem faixa de áudio — só o vídeo do scrcpy.',
  'pc-desktop': 'Só áudio interno do computador (Desktop Audio no OBS).',
  'pc-mic': 'Só microfone do computador (Mic/Aux no OBS).',
  'pc-both': 'Áudio interno + microfone juntos no OBS — sync ao parar.',
  phone: 'Áudio interno do celular via scrcpy — sem OBS.',
};

function connLabel() {
  return connection === 'wifi' ? 'Wi‑Fi' : 'USB';
}

function isPcAudio(audio) {
  return audio === 'pc-desktop' || audio === 'pc-mic' || audio === 'pc-both';
}

function isKnownAudioSource(audio) {
  return audio === 'none' || audio === 'phone' || isPcAudio(audio);
}

function getAudioSource() {
  const raw = els.audioSource?.value || 'pc-desktop';
  if (isKnownAudioSource(raw)) return raw;
  if (raw === 'pc') return 'pc-desktop';
  return els.useObsAudio?.checked ? 'pc-desktop' : 'none';
}

function withAudioSelected() {
  return getAudioSource() !== 'none';
}

function updateSessionCopy() {
  const isRecord = mode === 'record';
  const audio = getAudioSource();

  if (els.modeHint) {
    els.modeHint.textContent = isRecord
      ? 'Gravar salva um arquivo no PC.'
      : 'Capturar só espelha a tela — sem arquivo, sem OBS.';
  }

  if (els.qualityTitle) {
    els.qualityTitle.textContent = isRecord ? 'Qualidade do arquivo' : 'Qualidade do espelho';
  }
  if (els.qualityHint) {
    els.qualityHint.textContent = isRecord
      ? 'Bitrate, FPS e resolução do vídeo gravado pelo scrcpy.'
      : 'Afeta só o espelhamento na tela — nada é salvo.';
  }

  if (els.hintText) {
    if (!isRecord) {
      els.hintText.textContent = `Capturar via ${connLabel()}: espelha a tela, sem arquivo.`;
    } else if (audio === 'pc-desktop') {
      els.hintText.textContent = `Gravar via ${connLabel()}: vídeo + áudio interno do PC → sync.`;
    } else if (audio === 'pc-mic') {
      els.hintText.textContent = `Gravar via ${connLabel()}: vídeo + microfone do PC → sync.`;
    } else if (audio === 'pc-both') {
      els.hintText.textContent = `Gravar via ${connLabel()}: vídeo + interno e microfone → sync.`;
    } else if (audio === 'phone') {
      els.hintText.textContent = `Gravar via ${connLabel()}: vídeo + áudio interno do celular.`;
    } else {
      els.hintText.textContent = `Gravar via ${connLabel()}: só vídeo scrcpy.`;
    }
  }

  if (!isActive && els.filePath && isRecord) {
    const current = els.filePath.textContent || '';
    const isResult = /^(Salvo:|Próximo:)/.test(current);
    if (!isResult) {
      if (audio === 'pc-desktop') {
        els.filePath.textContent = 'Ao parar: salva vídeo e gera -sync com áudio interno';
      } else if (audio === 'pc-mic') {
        els.filePath.textContent = 'Ao parar: salva vídeo e gera -sync com microfone';
      } else if (audio === 'pc-both') {
        els.filePath.textContent = 'Ao parar: salva vídeo e gera -sync com interno + microfone';
      } else if (audio === 'phone') {
        els.filePath.textContent = 'Ao parar: salva vídeo com áudio do celular';
      } else {
        els.filePath.textContent = 'Ao parar: salva só o vídeo scrcpy';
      }
    }
  }

  if (!isActive && els.statusText) {
    els.statusText.textContent = isRecord ? 'Pronto para gravar' : 'Pronto para capturar';
  }
}

function normalizeWifiInput(raw) {
  return String(raw || '')
    .trim()
    .replace(/\s+/g, '')
    .replace(/,/g, '.')
    .replace(/[^0-9.:]/g, '');
}

function parseWifiAddress(raw) {
  const value = normalizeWifiInput(raw);
  if (!value) return { empty: true, valid: true, host: '', port: '5555', full: '' };

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
    full: isValid ? `${host}:${port}` : value,
  };
}

function updateWifiFieldUI() {
  if (!els.wifiAddress) return;
  const parsed = parseWifiAddress(els.wifiAddress.value);
  const hasText = !!els.wifiAddress.value.trim();

  if (els.btnClearWifi) els.btnClearWifi.hidden = !hasText;
  if (els.wifiPortHint) {
    els.wifiPortHint.style.opacity = parsed.empty || els.wifiAddress.value.includes(':') ? '0.35' : '1';
  }
  if (!els.wifiShell || !els.wifiHint) return;

  els.wifiShell.classList.remove('is-invalid', 'is-valid');
  els.wifiHint.classList.remove('error', 'ok');
  els.wifiAddress.setAttribute('aria-invalid', 'false');

  if (parsed.empty) {
    els.wifiHint.textContent = 'Digite só o IP. Na 1ª vez, deixe o USB ligado para autorizar.';
    if (els.btnConnectWifi) els.btnConnectWifi.disabled = isActive || isConnecting;
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
  if (els.btnConnectWifi) els.btnConnectWifi.disabled = isActive || isConnecting;
}

function getWifiAddressForApi() {
  const parsed = parseWifiAddress(els.wifiAddress.value);
  if (parsed.empty) return '';
  if (!parsed.valid) return els.wifiAddress.value.trim();
  return parsed.host;
}

function applyScreenBehaviorToCheckboxes() {
  els.stayAwake.checked = screenBehavior === 'keep';
  els.turnScreenOff.checked = screenBehavior === 'off';
}

function syncScreenBehaviorFromCheckboxes() {
  if (els.turnScreenOff.checked) screenBehavior = 'off';
  else if (els.stayAwake.checked) screenBehavior = 'keep';
  else screenBehavior = 'default';
}

function setScreenBehavior(next) {
  if (isActive) return;
  screenBehavior = next;
  applyScreenBehaviorToCheckboxes();
  syncScreenUI();
  window.api.saveSettings(getSettings());
}

function syncScreenUI() {
  const map = {
    keep: els.screenKeep,
    default: els.screenDefault,
    off: els.screenOff,
  };

  Object.entries(map).forEach(([key, btn]) => {
    if (!btn) return;
    const active = screenBehavior === key;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-checked', active ? 'true' : 'false');
    btn.disabled = isActive;
  });

  if (els.optionsHint) {
    els.optionsHint.classList.remove('warn');
    els.optionsHint.textContent = SCREEN_HINTS[screenBehavior] || '';
  }
}

function syncAudioUI() {
  const audio = getAudioSource();
  const pcSelected = isPcAudio(audio);

  const topMap = {
    none: els.audioOff,
    pc: els.audioPc,
    phone: els.audioPhone,
  };
  Object.entries(topMap).forEach(([key, btn]) => {
    if (!btn) return;
    const active = key === 'pc' ? pcSelected : audio === key;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-checked', active ? 'true' : 'false');
    btn.disabled = isActive;
  });

  if (els.pcAudioField) {
    els.pcAudioField.classList.toggle('hidden', !pcSelected || mode !== 'record');
  }

  const pcMap = {
    'pc-desktop': els.audioPcDesktop,
    'pc-mic': els.audioPcMic,
    'pc-both': els.audioPcBoth,
  };
  Object.entries(pcMap).forEach(([key, btn]) => {
    if (!btn) return;
    const active = audio === key;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-checked', active ? 'true' : 'false');
    btn.disabled = isActive;
  });

  if (els.audioSource) els.audioSource.value = audio;
  if (els.useObsAudio) els.useObsAudio.checked = pcSelected;
  if (els.audioHint) els.audioHint.textContent = AUDIO_HINTS[audio] || '';
}

function setAudioMode(next) {
  if (isActive) return;
  let audio = next;
  if (next === 'pc') {
    const current = getAudioSource();
    audio = isPcAudio(current) ? current : 'pc-desktop';
  } else if (!isKnownAudioSource(next)) {
    audio = next ? 'pc-desktop' : 'none';
  }
  if (els.audioSource) els.audioSource.value = audio;
  if (els.useObsAudio) els.useObsAudio.checked = isPcAudio(audio);
  syncAudioUI();
  applyModeUI();
  window.api.saveSettings(getSettings());
}

function getSettings() {
  applyScreenBehaviorToCheckboxes();
  const audioSource = mode === 'record' ? getAudioSource() : 'none';
  return {
    mode,
    connection,
    wifiAddress: getWifiAddressForApi(),
    bitrate: els.bitrate.value,
    maxFps: els.maxFps.value,
    maxSize: els.maxSize.value,
    format: els.format.value,
    audioSource,
    useObsAudio: isPcAudio(audioSource),
    stayAwake: screenBehavior === 'keep',
    turnScreenOff: screenBehavior === 'off',
  };
}

function setConnStatus(text, state = '') {
  if (!els.connStatus) return;
  els.connStatus.textContent = text;
  els.connStatus.classList.remove('ok', 'error', 'busy');
  if (state) els.connStatus.classList.add(state);
}

function applyModeUI() {
  const isRecord = mode === 'record';
  els.modeCapture.classList.toggle('active', !isRecord);
  els.modeRecord.classList.toggle('active', isRecord);
  els.modeCapture.setAttribute('aria-selected', (!isRecord).toString());
  els.modeRecord.setAttribute('aria-selected', isRecord.toString());

  document.querySelectorAll('.record-only').forEach((el) => {
    el.classList.toggle('hidden', !isRecord);
  });

  els.btnStartLabel.textContent = isRecord ? 'Iniciar gravação' : 'Iniciar captura';

  updateSessionCopy();
  syncAudioUI();
  syncScreenUI();
}

function applyConnectionUI() {
  const isWifi = connection === 'wifi';
  els.connUsb.classList.toggle('active', !isWifi);
  els.connWifi.classList.toggle('active', isWifi);
  els.connUsb.setAttribute('aria-selected', (!isWifi).toString());
  els.connWifi.setAttribute('aria-selected', isWifi.toString());

  document.querySelectorAll('.wifi-only').forEach((el) => {
    el.classList.toggle('hidden', !isWifi);
  });

  if (!isConnecting) {
    setConnStatus(
      isWifi
        ? 'Wi‑Fi: informe o IP ou conecte com USB na 1ª vez'
        : 'USB pronto — usa o cabo com depuração ativa'
    );
  }

  applyModeUI();
  updateWifiFieldUI();
}

function setControlsEnabled(enabled) {
  [
    els.modeCapture, els.modeRecord, els.connUsb, els.connWifi, els.wifiAddress,
    els.btnClearWifi, els.btnConnectWifi, els.bitrate, els.maxFps, els.maxSize, els.format,
    els.audioOff, els.audioPc, els.audioPhone, els.audioPcDesktop, els.audioPcMic, els.audioPcBoth,
    els.btnFolder, els.screenKeep, els.screenDefault, els.screenOff,
  ].forEach((el) => {
    if (el) el.disabled = !enabled;
  });
  syncAudioUI();
  syncScreenUI();
}

function setActiveState(active, info = {}) {
  isActive = active;
  setControlsEnabled(!active);

  if (active) {
    els.statusPill.classList.add('active');
    if (mode === 'record') {
      if (sessionAudioSource === 'pc-desktop') els.statusText.textContent = 'GRAVANDO · INTERNO';
      else if (sessionAudioSource === 'pc-mic') els.statusText.textContent = 'GRAVANDO · MIC';
      else if (sessionAudioSource === 'pc-both') els.statusText.textContent = 'GRAVANDO · AMBOS';
      else if (sessionAudioSource === 'phone') els.statusText.textContent = 'GRAVANDO · CELULAR';
      else els.statusText.textContent = 'GRAVANDO';
    } else {
      els.statusText.textContent = 'CAPTURANDO';
    }
    els.btnStart.style.display = 'none';
    els.btnStop.classList.add('visible');
    els.btnStop.disabled = false;
    if (info.videoPath) {
      els.filePath.textContent = info.videoPath;
    } else if (mode === 'capture') {
      els.filePath.textContent = 'Espelhamento ativo (sem arquivo)';
    } else if (sessionAudioSource === 'pc-desktop') {
      els.filePath.textContent = 'Gravando vídeo + áudio interno do PC…';
    } else if (sessionAudioSource === 'pc-mic') {
      els.filePath.textContent = 'Gravando vídeo + microfone do PC…';
    } else if (sessionAudioSource === 'pc-both') {
      els.filePath.textContent = 'Gravando vídeo + interno e microfone…';
    } else if (sessionAudioSource === 'phone') {
      els.filePath.textContent = 'Gravando vídeo + áudio do celular…';
    } else {
      els.filePath.textContent = 'Gravando só vídeo…';
    }
  } else {
    sessionHadAudio = false;
    sessionAudioSource = 'none';
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
  if (isActive || isConnecting) return;

  if (next === 'wifi') {
    const parsed = parseWifiAddress(els.wifiAddress.value);
    if (!parsed.empty && !parsed.valid) {
      updateWifiFieldUI();
      applyConnectionUI();
      connection = 'wifi';
      applyConnectionUI();
      els.wifiAddress.focus();
      return;
    }
  }

  connection = next;
  applyConnectionUI();
  isConnecting = true;
  setControlsEnabled(false);
  setConnStatus(
    next === 'wifi' ? 'Ativando conexão Wi‑Fi…' : 'Ativando conexão USB…',
    'busy'
  );

  const result = await window.api.activateConnection({
    connection: next,
    wifiAddress: getWifiAddressForApi(),
  });

  isConnecting = false;
  window.api.saveSettings(getSettings());

  if (result.wifiAddress && els.wifiAddress) {
    els.wifiAddress.value = String(result.wifiAddress).replace(/:5555$/, '');
  }

  setControlsEnabled(!isActive);
  applyConnectionUI();
  updateWifiFieldUI();

  if (result.success) {
    setConnStatus(result.message || (next === 'wifi' ? 'Wi‑Fi conectado' : 'USB ativo'), 'ok');
  } else {
    setConnStatus(result.message || 'Falha na conexão', 'error');
  }
}

els.connUsb.addEventListener('click', () => switchConnection('usb'));
els.connWifi.addEventListener('click', () => switchConnection('wifi'));

[els.screenKeep, els.screenDefault, els.screenOff].forEach((btn) => {
  if (!btn) return;
  btn.addEventListener('click', () => setScreenBehavior(btn.dataset.screen));
});

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
    const start = els.wifiAddress.selectionStart || 0;
    const end = els.wifiAddress.selectionEnd || 0;
    const value = els.wifiAddress.value;
    els.wifiAddress.value = normalizeWifiInput(value.slice(0, start) + cleaned + value.slice(end));
    updateWifiFieldUI();
  });

  els.wifiAddress.addEventListener('blur', () => {
    const parsed = parseWifiAddress(els.wifiAddress.value);
    if (parsed.valid && !parsed.empty) els.wifiAddress.value = parsed.host;
    updateWifiFieldUI();
    window.api.saveSettings(getSettings());
  });

  els.wifiAddress.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (els.btnConnectWifi && !els.btnConnectWifi.disabled) els.btnConnectWifi.click();
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

['bitrate', 'maxFps', 'maxSize', 'format'].forEach((id) => {
  els[id].addEventListener('change', () => window.api.saveSettings(getSettings()));
});

if (els.audioOff) els.audioOff.addEventListener('click', () => setAudioMode('none'));
if (els.audioPc) els.audioPc.addEventListener('click', () => setAudioMode('pc'));
if (els.audioPhone) els.audioPhone.addEventListener('click', () => setAudioMode('phone'));
if (els.audioPcDesktop) els.audioPcDesktop.addEventListener('click', () => setAudioMode('pc-desktop'));
if (els.audioPcMic) els.audioPcMic.addEventListener('click', () => setAudioMode('pc-mic'));
if (els.audioPcBoth) els.audioPcBoth.addEventListener('click', () => setAudioMode('pc-both'));

els.btnFolder.addEventListener('click', async () => {
  const result = await window.api.chooseFolder();
  if (result) {
    els.recordDir.value = result.dir;
    els.filePath.textContent = 'Próximo: ' + result.preview;
  }
});

els.btnStart.addEventListener('click', async () => {
  if (connection === 'wifi') {
    const parsed = parseWifiAddress(els.wifiAddress.value);
    if (!parsed.empty && !parsed.valid) {
      updateWifiFieldUI();
      els.wifiAddress.focus();
      return;
    }
  }

  els.btnStart.disabled = true;
  els.btnStartLabel.textContent = 'Iniciando...';

  const settings = getSettings();
  sessionAudioSource = settings.mode === 'record' ? settings.audioSource : 'none';
  sessionHadAudio = isPcAudio(sessionAudioSource);
  const result = await window.api.startRecording(settings);

  if (result.success) {
    setActiveState(true, result);
  } else {
    sessionHadAudio = false;
    sessionAudioSource = 'none';
    alert(result.message);
    els.btnStart.disabled = false;
    applyModeUI();
  }
});

els.btnStop.addEventListener('click', async () => {
  els.btnStop.disabled = true;
  const syncing = isPcAudio(sessionAudioSource);
  els.btnStop.textContent = syncing ? 'Sincronizando…' : 'Parando…';
  if (syncing) els.statusText.textContent = 'SINCRONIZANDO';

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
    if (isActive) {
      if (mode === 'record') {
        if (sessionAudioSource === 'pc-desktop') els.statusText.textContent = 'GRAVANDO · INTERNO';
        else if (sessionAudioSource === 'pc-mic') els.statusText.textContent = 'GRAVANDO · MIC';
        else if (sessionAudioSource === 'pc-both') els.statusText.textContent = 'GRAVANDO · AMBOS';
        else if (sessionAudioSource === 'phone') els.statusText.textContent = 'GRAVANDO · CELULAR';
        else els.statusText.textContent = 'GRAVANDO';
      } else {
        els.statusText.textContent = 'CAPTURANDO';
      }
    }
  }

  els.btnStop.disabled = false;
  els.btnStop.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
    Parar
  `;
});

window.api.onRecordingStarted((data) => {
  if (data.mode) mode = data.mode;
  if (
    data.audioSource === 'none'
    || data.audioSource === 'pc-desktop'
    || data.audioSource === 'pc-mic'
    || data.audioSource === 'pc-both'
    || data.audioSource === 'phone'
  ) {
    sessionAudioSource = data.audioSource;
  } else if (data.audioSource === 'pc') {
    sessionAudioSource = 'pc-desktop';
  } else if (typeof data.useObsAudio === 'boolean') {
    sessionAudioSource = data.useObsAudio ? 'pc-desktop' : 'none';
  } else if (!sessionHadAudio) {
    sessionAudioSource = mode === 'record' ? getAudioSource() : 'none';
  }
  sessionHadAudio = isPcAudio(sessionAudioSource);
  setActiveState(true, data);
});

window.api.onRecordingStopped(() => {
  setActiveState(false);
});

window.api.onRecordingError((msg) => {
  alert('Erro: ' + msg);
  setActiveState(false);
});

if (window.api.onStatusText) {
  window.api.onStatusText((text) => {
    if (els.statusText && text) els.statusText.textContent = text;
    if (els.btnStartLabel && !isActive && text) els.btnStartLabel.textContent = text;
  });
}

els.btnCopyRemote.addEventListener('click', async () => {
  const url = els.remoteUrl.textContent;
  if (!url || url.includes('…') || url.includes('falhou') || url.includes('indisponível')) return;
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
    const audio = (
      s.audioSource === 'none'
      || s.audioSource === 'pc-desktop'
      || s.audioSource === 'pc-mic'
      || s.audioSource === 'pc-both'
      || s.audioSource === 'phone'
    )
      ? s.audioSource
      : (s.audioSource === 'pc'
        ? 'pc-desktop'
        : (s.useObsAudio === false ? 'none' : 'pc-desktop'));
    if (els.audioSource) els.audioSource.value = audio;
    els.useObsAudio.checked = isPcAudio(audio);
    els.stayAwake.checked = s.stayAwake !== false;
    els.turnScreenOff.checked = !!s.turnScreenOff;
    if (els.stayAwake.checked && els.turnScreenOff.checked) {
      els.turnScreenOff.checked = false;
    }
    syncScreenBehaviorFromCheckboxes();
  }
  applyConnectionUI();
  syncScreenUI();
  updateWifiFieldUI();
  if (status.isRecording) {
    if (
      status.audioSource === 'none'
      || status.audioSource === 'pc-desktop'
      || status.audioSource === 'pc-mic'
      || status.audioSource === 'pc-both'
      || status.audioSource === 'phone'
    ) {
      sessionAudioSource = status.audioSource;
    } else if (status.audioSource === 'pc') {
      sessionAudioSource = 'pc-desktop';
    } else {
      sessionAudioSource = status.useObsAudio ? 'pc-desktop' : 'none';
    }
    sessionHadAudio = isPcAudio(sessionAudioSource);
    if (status.mode) mode = status.mode;
    setActiveState(true, { videoPath: status.videoPath });
  }
  if (status.remote) showRemoteInfo(status.remote);
});

setTimeout(() => {
  window.api.getRemoteInfo().then(showRemoteInfo).catch(() => {});
}, 800);
