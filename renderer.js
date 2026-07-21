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
  updateVersion: document.getElementById('updateVersion'),
  updateStatus: document.getElementById('updateStatus'),
  updateProgress: document.getElementById('updateProgress'),
  updateProgressBar: document.getElementById('updateProgressBar'),
  btnCheckUpdate: document.getElementById('btnCheckUpdate'),
  btnInstallUpdate: document.getElementById('btnInstallUpdate'),
  btnOpenReleases: document.getElementById('btnOpenReleases'),
  transferDevice: document.getElementById('transferDevice'),
  transferPath: document.getElementById('transferPath'),
  transferList: document.getElementById('transferList'),
  transferHint: document.getElementById('transferHint'),
  transferLibrary: document.getElementById('transferLibrary'),
  transferCats: document.getElementById('transferCats'),
  transferViews: document.getElementById('transferViews'),
  transferPreview: document.getElementById('transferPreview'),
  transferPreviewFrame: document.getElementById('transferPreviewFrame'),
  transferPreviewIcon: document.getElementById('transferPreviewIcon'),
  transferPreviewImg: document.getElementById('transferPreviewImg'),
  transferPreviewName: document.getElementById('transferPreviewName'),
  transferPreviewInfo: document.getElementById('transferPreviewInfo'),
  btnTransferUp: document.getElementById('btnTransferUp'),
  btnTransferRefresh: document.getElementById('btnTransferRefresh'),
  btnTransferPush: document.getElementById('btnTransferPush'),
  btnTransferPull: document.getElementById('btnTransferPull'),
  transferPanel: document.getElementById('transferPanel'),
  btnAutoJean15: document.getElementById('btnAutoJean15'),
  btnAutoStop: document.getElementById('btnAutoStop'),
  automationHint: document.getElementById('automationHint'),
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
  document.querySelectorAll('.phy-select').forEach((wrap) => {
    const trigger = wrap.querySelector('.phy-select-trigger');
    const select = wrap.querySelector('select');
    if (trigger) trigger.disabled = !enabled || !!select?.disabled;
    wrap.classList.toggle('is-disabled', !enabled);
  });
  syncAudioUI();
  syncScreenUI();
}

/** Menus físicos — o popup nativo do Windows não herda o tema. */
function enhancePhysicalSelects() {
  const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');

  document.querySelectorAll('select').forEach((select) => {
    if (select.dataset.phyEnhanced === '1') return;
    select.dataset.phyEnhanced = '1';
    select.classList.add('phy-select-native');
    select.setAttribute('tabindex', '-1');
    select.setAttribute('aria-hidden', 'true');

    const wrap = document.createElement('div');
    wrap.className = 'phy-select';
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'phy-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    if (select.id) trigger.id = `${select.id}Trigger`;
    trigger.innerHTML = '<span class="phy-select-value"></span><span class="phy-select-caret" aria-hidden="true"></span>';

    const valueEl = trigger.querySelector('.phy-select-value');
    const menu = document.createElement('div');
    menu.className = 'phy-select-menu';
    menu.setAttribute('role', 'listbox');
    document.body.appendChild(menu);

    function selectedOption() {
      return select.options[select.selectedIndex] || select.options[0];
    }

    function syncFromSelect() {
      const opt = selectedOption();
      valueEl.textContent = opt ? opt.textContent : '';
      menu.querySelectorAll('.phy-select-option').forEach((btn) => {
        btn.classList.toggle('is-selected', btn.dataset.value === select.value);
      });
      trigger.disabled = !!select.disabled;
    }

    function rebuildOptions() {
      menu.innerHTML = '';
      Array.from(select.options).forEach((opt) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'phy-select-option';
        btn.setAttribute('role', 'option');
        btn.dataset.value = opt.value;
        btn.textContent = opt.textContent;
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          select.value = opt.value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          syncFromSelect();
          closeMenu();
        });
        menu.appendChild(btn);
      });
      syncFromSelect();
    }

    function positionMenu() {
      const r = trigger.getBoundingClientRect();
      const menuH = Math.min(240, window.innerHeight * 0.42);
      const spaceBelow = window.innerHeight - r.bottom - 8;
      const openUp = spaceBelow < Math.min(menuH, 160) && r.top > spaceBelow;

      menu.style.left = `${Math.round(r.left)}px`;
      menu.style.width = `${Math.round(r.width)}px`;
      menu.style.maxHeight = `${Math.round(menuH)}px`;

      if (openUp) {
        menu.style.top = 'auto';
        menu.style.bottom = `${Math.round(window.innerHeight - r.top + 6)}px`;
      } else {
        menu.style.bottom = 'auto';
        menu.style.top = `${Math.round(r.bottom + 6)}px`;
      }
    }

    function closeMenu() {
      wrap.classList.remove('is-open');
      menu.classList.remove('is-visible');
      trigger.setAttribute('aria-expanded', 'false');
    }

    function openMenu() {
      if (select.disabled || trigger.disabled) return;
      document.querySelectorAll('.phy-select.is-open').forEach((other) => {
        if (other !== wrap) other.dispatchEvent(new CustomEvent('phy-close'));
      });
      positionMenu();
      wrap.classList.add('is-open');
      menu.classList.add('is-visible');
      trigger.setAttribute('aria-expanded', 'true');
      const selected = menu.querySelector('.phy-select-option.is-selected');
      if (selected) selected.focus();
    }

    wrap.addEventListener('phy-close', closeMenu);

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      if (wrap.classList.contains('is-open')) closeMenu();
      else openMenu();
    });

    trigger.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMenu();
      }
    });

    menu.addEventListener('keydown', (e) => {
      const options = Array.from(menu.querySelectorAll('.phy-select-option'));
      const idx = options.indexOf(document.activeElement);
      if (e.key === 'Escape') {
        e.preventDefault();
        closeMenu();
        trigger.focus();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        options[Math.min(options.length - 1, idx + 1)]?.focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        options[Math.max(0, idx - 1)]?.focus();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        document.activeElement?.click();
      }
    });

    Object.defineProperty(select, 'value', {
      configurable: true,
      get() { return valueDesc.get.call(this); },
      set(v) {
        valueDesc.set.call(this, v);
        syncFromSelect();
      },
    });

    const nativeDisabled = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'disabled');
    Object.defineProperty(select, 'disabled', {
      configurable: true,
      get() { return nativeDisabled.get.call(this); },
      set(v) {
        nativeDisabled.set.call(this, v);
        trigger.disabled = !!v;
        if (v) closeMenu();
      },
    });

    wrap.insertBefore(trigger, select);
    rebuildOptions();
    select._phySync = syncFromSelect;
  });

  document.addEventListener('click', (e) => {
    if (e.target.closest('.phy-select') || e.target.closest('.phy-select-menu')) return;
    document.querySelectorAll('.phy-select.is-open').forEach((wrap) => {
      wrap.dispatchEvent(new CustomEvent('phy-close'));
    });
  });

  window.addEventListener('resize', () => {
    document.querySelectorAll('.phy-select.is-open').forEach((wrap) => {
      wrap.dispatchEvent(new CustomEvent('phy-close'));
    });
  });

  document.querySelector('.panel')?.addEventListener('scroll', () => {
    document.querySelectorAll('.phy-select.is-open').forEach((wrap) => {
      wrap.dispatchEvent(new CustomEvent('phy-close'));
    });
  }, { passive: true });
}

enhancePhysicalSelects();

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

const RELEASES_URL = 'https://github.com/clevencode/clevenrec/releases';
let updateUiState = { currentVersion: '—' };

function renderUpdateUI(state = {}) {
  updateUiState = { ...updateUiState, ...state };
  const current = updateUiState.currentVersion || '—';
  const available = updateUiState.version && updateUiState.version !== current ? updateUiState.version : null;
  els.updateVersion.textContent = available
    ? `Versão ${current} → ${available}`
    : `Versão ${current}`;

  if (updateUiState.message) {
    els.updateStatus.textContent = updateUiState.message;
  }

  const isDownloading = updateUiState.status === 'downloading';
  els.updateProgress.classList.toggle('is-visible', isDownloading);
  if (isDownloading) {
    els.updateProgressBar.style.width = `${updateUiState.percent || 0}%`;
  } else {
    els.updateProgressBar.style.width = '0%';
  }

  const canInstall = updateUiState.status === 'downloaded';
  els.btnInstallUpdate.classList.toggle('is-visible', canInstall);

  const isBusy = updateUiState.status === 'checking' || updateUiState.status === 'downloading';
  els.btnCheckUpdate.disabled = isBusy;
  els.btnCheckUpdate.textContent = isBusy ? 'Verificando…' : 'Verificar';
}

if (window.api.onUpdateStatus) {
  window.api.onUpdateStatus((state) => renderUpdateUI(state));
}

els.btnCheckUpdate.addEventListener('click', async () => {
  renderUpdateUI({ status: 'checking', message: 'Verificando atualizações…' });
  try {
    const result = await window.api.checkForUpdates();
    if (result?.status) renderUpdateUI(result.status);
  } catch (error) {
    renderUpdateUI({
      status: 'error',
      message: error?.message || 'Falha ao verificar atualização.',
    });
  }
});

els.btnInstallUpdate.addEventListener('click', async () => {
  els.btnInstallUpdate.disabled = true;
  els.btnInstallUpdate.textContent = 'Reiniciando…';
  try {
    await window.api.installUpdate();
  } catch (error) {
    els.updateStatus.textContent = error?.message || 'Falha ao instalar atualização.';
    els.btnInstallUpdate.disabled = false;
    els.btnInstallUpdate.textContent = 'Instalar e reiniciar';
  }
});

els.btnOpenReleases.addEventListener('click', () => {
  window.api.openExternal(RELEASES_URL);
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

Promise.all([
  window.api.getAppVersion().catch(() => null),
  window.api.getUpdateStatus().catch(() => null),
]).then(([version, status]) => {
  renderUpdateUI({
    ...(status || {}),
    currentVersion: version || status?.currentVersion || '—',
  });
});

/* —— Transferência PC ↔ celular (painel direito) —— */
let transferSelected = null;
let transferBusy = false;
let transferEntries = [];
let transferCurrentPath = '/sdcard/DCIM/Camera';
let transferCategory = 'images';
let transferLibraryId = 'images';
let transferViewMode = (localStorage.getItem('clevenrec.transferView') === 'grid') ? 'grid' : 'list';
let transferPreviewToken = 0;

const MEDIA_LIBRARY = {
  images: { path: '/sdcard/DCIM/Camera', category: 'images', label: 'Imagens' },
  videos: { path: '/sdcard/Movies', category: 'videos', label: 'Vídeos' },
  audio: { path: '/sdcard/Music', category: 'audio', label: 'Áudio' },
  docs: { path: '/sdcard/Documents', category: 'docs', label: 'Docs' },
  downloads: { path: '/sdcard/Download', category: 'all', label: 'Downloads' },
};

const TRANSFER_CAT_ORDER = ['folders', 'images', 'videos', 'audio', 'docs', 'other'];
const TRANSFER_CAT_LABELS = {
  folders: 'Pastas',
  images: 'Imagens',
  videos: 'Vídeos',
  audio: 'Áudio',
  docs: 'Documentos',
  other: 'Outros',
};

const TRANSFER_CAT_ICONS = {
  folders: '▸',
  images: '▣',
  videos: '▶',
  audio: '♫',
  docs: '▤',
  other: '·',
};

const TRANSFER_EXT = {
  images: new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'heic', 'heif', 'svg', 'tif', 'tiff', 'raw', 'dng']),
  videos: new Set(['mp4', 'mkv', 'avi', 'mov', 'wmv', 'webm', '3gp', 'm4v', 'flv', 'ts', 'mpeg', 'mpg']),
  audio: new Set(['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a', 'wma', 'opus', 'amr']),
  docs: new Set([
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'rtf', 'odt', 'ods', 'odp',
    'csv', 'json', 'xml', 'md', 'epub', 'pages', 'numbers', 'key',
  ]),
};

function setTransferHint(text) {
  if (els.transferHint) els.transferHint.textContent = text || '';
}

function setTransferBusy(busy, label) {
  transferBusy = busy;
  const controls = [
    els.btnTransferRefresh,
    els.btnTransferPush,
    els.btnTransferPull,
    els.btnTransferUp,
    els.transferPath,
  ];
  controls.forEach((el) => {
    if (el) el.disabled = !!busy;
  });
  if (els.transferLibrary) {
    els.transferLibrary.querySelectorAll('button').forEach((btn) => {
      btn.disabled = !!busy;
    });
  }
  if (els.transferCats) {
    els.transferCats.querySelectorAll('button').forEach((btn) => {
      btn.disabled = !!busy;
    });
  }
  if (els.transferViews) {
    els.transferViews.querySelectorAll('button').forEach((btn) => {
      btn.disabled = !!busy;
    });
  }
  if (els.transferPanel) {
    els.transferPanel.classList.toggle('is-busy', !!busy);
  }
  if (label) setTransferHint(label);
}

function formatSize(n) {
  if (n == null || Number.isNaN(n)) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function joinRemote(dir, name) {
  const base = String(dir || '').replace(/\/+$/, '') || '/';
  return base === '/' ? `/${name}` : `${base}/${name}`;
}

function fileExtension(name) {
  const base = String(name || '').split(/[\\/]/).pop() || '';
  const dot = base.lastIndexOf('.');
  if (dot <= 0 || dot === base.length - 1) return '';
  return base.slice(dot + 1).toLowerCase();
}

function syncTransferViewButtons() {
  if (!els.transferViews) return;
  els.transferViews.querySelectorAll('[data-view]').forEach((btn) => {
    const mode = btn.getAttribute('data-view') || 'list';
    const active = mode === transferViewMode;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  if (els.transferList) {
    els.transferList.classList.toggle('is-grid', transferViewMode === 'grid');
    els.transferList.dataset.view = transferViewMode;
  }
}

function setTransferViewMode(mode) {
  transferViewMode = mode === 'grid' ? 'grid' : 'list';
  try {
    localStorage.setItem('clevenrec.transferView', transferViewMode);
  } catch (_) { /* ignore quota / private mode */ }
  syncTransferViewButtons();
  renderTransferList(transferEntries, transferCurrentPath);
}

function transferIconFor(entry) {
  const cat = categorizeEntry(entry);
  return TRANSFER_CAT_ICONS[cat] || TRANSFER_CAT_ICONS.other;
}

function categorizeEntry(entry) {
  if (entry.isDir) return 'folders';
  const ext = fileExtension(entry.name);
  if (!ext) return 'other';
  if (TRANSFER_EXT.images.has(ext)) return 'images';
  if (TRANSFER_EXT.videos.has(ext)) return 'videos';
  if (TRANSFER_EXT.audio.has(ext)) return 'audio';
  if (TRANSFER_EXT.docs.has(ext)) return 'docs';
  return 'other';
}

function countByCategory(entries) {
  const counts = Object.fromEntries(TRANSFER_CAT_ORDER.map((cat) => [cat, 0]));
  entries.forEach((entry) => {
    const cat = entry.category || categorizeEntry(entry);
    if (counts[cat] != null) counts[cat] += 1;
  });
  return counts;
}

function syncTransferCatButtons(counts = null) {
  if (!els.transferCats) return;
  const tallies = counts || countByCategory(
    transferEntries.map((entry) => ({ ...entry, category: categorizeEntry(entry) }))
  );
  els.transferCats.querySelectorAll('[data-cat]').forEach((btn) => {
    const cat = btn.getAttribute('data-cat') || 'all';
    btn.classList.toggle('is-active', cat === transferCategory);
    const base = btn.dataset.label || btn.textContent.split('·')[0].trim();
    btn.dataset.label = base;
    if (cat === 'all') {
      const total = transferEntries.length;
      btn.textContent = total ? `${base} · ${total}` : base;
    } else {
      const n = tallies[cat] || 0;
      btn.textContent = n ? `${base} · ${n}` : base;
    }
  });
}

function syncTransferLibraryButtons() {
  if (!els.transferLibrary) return;
  els.transferLibrary.querySelectorAll('[data-lib]').forEach((btn) => {
    const id = btn.getAttribute('data-lib') || '';
    btn.classList.toggle('is-active', id === transferLibraryId);
  });
}

function clearTransferPreview(message = 'Selecione um arquivo', info = 'Somente visualização') {
  if (els.transferPreview) els.transferPreview.classList.add('transfer-preview-empty');
  if (els.transferPreviewFrame) els.transferPreviewFrame.classList.remove('has-image');
  if (els.transferPreviewImg) {
    els.transferPreviewImg.removeAttribute('src');
    els.transferPreviewImg.alt = '';
  }
  if (els.transferPreviewIcon) els.transferPreviewIcon.textContent = '▣';
  if (els.transferPreviewName) els.transferPreviewName.textContent = message;
  if (els.transferPreviewInfo) els.transferPreviewInfo.textContent = info;
}

async function showTransferPreview(entry, remotePath) {
  if (!els.transferPreview) return;
  const token = ++transferPreviewToken;
  const cat = categorizeEntry(entry);
  const kind = entry.isDir ? 'Pasta' : (TRANSFER_CAT_LABELS[cat] || 'Arquivo');
  const size = formatSize(entry.size);
  els.transferPreview.classList.remove('transfer-preview-empty');
  if (els.transferPreviewName) els.transferPreviewName.textContent = entry.name;
  if (els.transferPreviewInfo) {
    els.transferPreviewInfo.textContent = [kind, size, remotePath].filter(Boolean).join(' · ');
  }
  if (els.transferPreviewIcon) els.transferPreviewIcon.textContent = transferIconFor(entry);
  if (els.transferPreviewFrame) els.transferPreviewFrame.classList.remove('has-image');
  if (els.transferPreviewImg) {
    els.transferPreviewImg.removeAttribute('src');
    els.transferPreviewImg.alt = entry.name;
  }

  if (entry.isDir || cat !== 'images' || !window.api.transferPreview) return;

  if (els.transferPreviewInfo) {
    els.transferPreviewInfo.textContent = [kind, size, 'carregando preview…'].filter(Boolean).join(' · ');
  }
  try {
    const result = await window.api.transferPreview({
      remotePath,
      size: entry.size,
    });
    if (token !== transferPreviewToken) return;
    if (result?.success && result.dataUrl && els.transferPreviewImg) {
      els.transferPreviewImg.src = result.dataUrl;
      els.transferPreviewImg.alt = entry.name;
      if (els.transferPreviewFrame) els.transferPreviewFrame.classList.add('has-image');
      if (els.transferPreviewInfo) {
        els.transferPreviewInfo.textContent = [kind, formatSize(result.size || entry.size), remotePath]
          .filter(Boolean)
          .join(' · ');
      }
    } else if (els.transferPreviewInfo) {
      els.transferPreviewInfo.textContent = [
        kind,
        size,
        result?.message || 'Preview indisponível',
      ].filter(Boolean).join(' · ');
    }
  } catch (err) {
    if (token !== transferPreviewToken) return;
    if (els.transferPreviewInfo) {
      els.transferPreviewInfo.textContent = [kind, size, err?.message || 'Falha no preview']
        .filter(Boolean)
        .join(' · ');
    }
  }
}

function selectTransferEntry(entry, currentPath) {
  if (!els.transferList) return;
  const remotePath = joinRemote(currentPath, entry.name);
  els.transferList.querySelectorAll('li[data-name]').forEach((n) => {
    n.classList.toggle('is-selected', n.dataset.name === entry.name);
  });
  transferSelected = {
    name: entry.name,
    isDir: !!entry.isDir,
    path: remotePath,
    category: categorizeEntry(entry),
    size: entry.size,
  };
  const kind = entry.isDir ? 'Pasta' : (TRANSFER_CAT_LABELS[transferSelected.category] || 'Arquivo');
  setTransferHint(`${kind} selecionado · toque em Baixar para trazer ao PC`);
  if (els.btnTransferPull) els.btnTransferPull.disabled = !!transferBusy;
  showTransferPreview(entry, remotePath);
}

function openTransferFolder(entry, currentPath) {
  if (!entry?.isDir) return;
  els.transferPath.value = joinRemote(currentPath, entry.name);
  refreshTransferList();
}

function appendTransferEntry(entry, currentPath) {
  const li = document.createElement('li');
  const cat = categorizeEntry(entry);
  li.className = entry.isDir ? 'is-dir' : `is-file is-${cat}`;
  li.dataset.name = entry.name;
  li.dataset.dir = entry.isDir ? '1' : '0';
  li.dataset.cat = cat;
  li.setAttribute('role', 'option');
  li.setAttribute('aria-selected', 'false');
  li.title = entry.isDir
    ? `${entry.name} · pasta`
    : `${entry.name} · ${TRANSFER_CAT_LABELS[cat] || 'Outros'}`;
  li.innerHTML = `
    <span class="xfer-icon" aria-hidden="true">${transferIconFor(entry)}</span>
    <span class="xfer-name"></span>
    <span class="xfer-meta"></span>
  `;
  li.querySelector('.xfer-name').textContent = entry.name;
  const metaParts = [];
  if (entry.isDir) metaParts.push('pasta');
  else {
    if (transferViewMode === 'list') metaParts.push(TRANSFER_CAT_LABELS[cat] || 'Outros');
    const size = formatSize(entry.size);
    if (size) metaParts.push(size);
  }
  li.querySelector('.xfer-meta').textContent = metaParts.join(' · ');

  // Padrão file manager: click seleciona; duplo clique abre pasta
  li.addEventListener('click', () => {
    selectTransferEntry(entry, currentPath);
    li.setAttribute('aria-selected', 'true');
  });
  li.addEventListener('dblclick', (e) => {
    e.preventDefault();
    if (entry.isDir) openTransferFolder(entry, currentPath);
    else {
      selectTransferEntry(entry, currentPath);
      pullFromDevice();
    }
  });

  if (transferSelected?.path === joinRemote(currentPath, entry.name)) {
    li.classList.add('is-selected');
    li.setAttribute('aria-selected', 'true');
  }

  els.transferList.appendChild(li);
}

function renderTransferList(entries = [], currentPath) {
  if (!els.transferList) return;
  const previousSelectedPath = transferSelected?.path || null;
  transferEntries = Array.isArray(entries) ? entries : [];
  transferCurrentPath = currentPath || transferCurrentPath;
  syncTransferViewButtons();

  const enriched = transferEntries.map((entry) => ({
    ...entry,
    category: categorizeEntry(entry),
  }));
  const counts = countByCategory(enriched);
  syncTransferCatButtons(counts);

  const visible = transferCategory === 'all'
    ? enriched
    : enriched.filter((entry) => entry.category === transferCategory);

  if (!transferEntries.length) {
    transferSelected = null;
    clearTransferPreview();
    els.transferList.innerHTML = '<li class="transfer-empty">Pasta vazia neste caminho.</li>';
    setTransferHint('Envie arquivos do PC ou escolha outra biblioteca acima.');
    return;
  }
  if (!visible.length) {
    transferSelected = null;
    clearTransferPreview('Nada neste filtro', `Troque o filtro ou a biblioteca (${TRANSFER_CAT_LABELS[transferCategory] || transferCategory})`);
    els.transferList.innerHTML = `<li class="transfer-empty">Nada em ${TRANSFER_CAT_LABELS[transferCategory] || transferCategory} nesta pasta.</li>`;
    setTransferHint('Troque o filtro ou abra outra pasta.');
    return;
  }

  els.transferList.innerHTML = '';
  // Restaura seleção se o item ainda existe
  if (previousSelectedPath) {
    const stillThere = enriched.find((e) => joinRemote(transferCurrentPath, e.name) === previousSelectedPath);
    transferSelected = stillThere
      ? {
        name: stillThere.name,
        isDir: !!stillThere.isDir,
        path: previousSelectedPath,
        category: stillThere.category,
        size: stillThere.size,
      }
      : null;
    if (stillThere) showTransferPreview(stillThere, previousSelectedPath);
    else clearTransferPreview();
  } else {
    transferSelected = null;
    clearTransferPreview();
  }

  if (transferCategory === 'all') {
    TRANSFER_CAT_ORDER.forEach((cat) => {
      const group = visible.filter((entry) => entry.category === cat);
      if (!group.length) return;
      // Em grade, os ícones já separam tipos — cabeçalhos só na lista
      if (transferViewMode === 'list') {
        const header = document.createElement('li');
        header.className = 'xfer-section';
        header.textContent = `${TRANSFER_CAT_LABELS[cat]} · ${group.length}`;
        els.transferList.appendChild(header);
      }
      group.forEach((entry) => appendTransferEntry(entry, transferCurrentPath));
    });
  } else {
    visible.forEach((entry) => appendTransferEntry(entry, transferCurrentPath));
  }

  const countsHint = TRANSFER_CAT_ORDER
    .map((cat) => (counts[cat] ? `${TRANSFER_CAT_LABELS[cat]} ${counts[cat]}` : null))
    .filter(Boolean);
  if (!transferSelected) {
    const viewLabel = transferViewMode === 'grid' ? 'grade' : 'lista';
    setTransferHint(countsHint.length
      ? `${enriched.length} item(ns) · ${viewLabel}: ${countsHint.join(' · ')}`
      : `${enriched.length} item(ns) em ${transferCurrentPath}`);
  }
}

async function refreshTransferList(options = {}) {
  if (!els.transferPath) return;
  if (transferBusy && !options.force) return;
  const pathValue = els.transferPath.value.trim() || '/sdcard/Download';
  const restoreBusy = transferBusy;
  setTransferBusy(true, 'Listando pasta no celular…');
  try {
    const result = await window.api.transferList({ path: pathValue });
    if (!result?.success) {
      els.transferDevice.textContent = 'Sem dispositivo';
      transferEntries = [];
      transferSelected = null;
      clearTransferPreview('Sem dispositivo', result?.message || 'Conecte o celular via USB');
      els.transferList.innerHTML = `<li class="transfer-empty">${result?.message || 'Falha ao listar.'}</li>`;
      setTransferHint(result?.message || 'Conecte o celular via USB com depuração USB.');
      return;
    }
    els.transferPath.value = result.path;
    els.transferDevice.textContent = result.serial || 'conectado';
    renderTransferList(result.entries || [], result.path);
  } catch (err) {
    setTransferHint(err?.message || 'Erro ao listar pasta.');
  } finally {
    setTransferBusy(false);
    if (restoreBusy && options.keepBusyLabel) {
      setTransferHint(options.keepBusyLabel);
    }
  }
}

async function pushToDevice() {
  if (transferBusy) return;
  setTransferBusy(true, 'Escolhendo arquivos no PC…');
  try {
    const picked = await window.api.transferChooseFiles();
    if (!picked?.success) {
      setTransferHint('Envio cancelado.');
      return;
    }
    const total = picked.files.length;
    setTransferHint(`Enviando 1/${total}…`);
    const result = await window.api.transferPush({
      remoteDir: els.transferPath.value.trim() || '/sdcard/Download',
      files: picked.files,
    });
    const msg = result?.message || (result?.success ? 'Enviado.' : 'Falha no envio.');
    if (result?.success) {
      // Libera busy antes do refresh (evita early-return)
      setTransferBusy(false);
      await refreshTransferList({ force: true });
      setTransferHint(msg);
      return;
    }
    setTransferHint(msg);
  } catch (err) {
    setTransferHint(err?.message || 'Falha no envio.');
  } finally {
    setTransferBusy(false);
  }
}

async function pullFromDevice() {
  if (transferBusy) return;
  if (!transferSelected) {
    setTransferHint('Selecione um arquivo ou pasta (clique na lista).');
    return;
  }
  setTransferBusy(true, 'Escolhendo pasta no PC…');
  try {
    const dest = await window.api.transferChooseSaveDir();
    if (!dest?.success) {
      setTransferHint('Download cancelado.');
      return;
    }
    setTransferHint(`Baixando ${transferSelected.name}…`);
    const result = await window.api.transferPull({
      remotePath: transferSelected.path,
      localDir: dest.dir,
    });
    if (result?.success) {
      setTransferHint(result.message || `Baixado → ${dest.dir}`);
      if (window.api.openPath) {
        await window.api.openPath(dest.dir);
      }
    } else {
      setTransferHint(result?.message || 'Falha no download.');
    }
  } catch (err) {
    setTransferHint(err?.message || 'Falha no download.');
  } finally {
    setTransferBusy(false);
  }
}

if (els.btnTransferRefresh) {
  els.btnTransferRefresh.addEventListener('click', () => refreshTransferList());
}
if (els.btnTransferUp) {
  els.btnTransferUp.addEventListener('click', () => {
    const cur = (els.transferPath.value || '/sdcard').replace(/\/+$/, '');
    if (cur === '/sdcard' || cur === '/storage/emulated/0' || cur === '/') return;
    const idx = cur.lastIndexOf('/');
    els.transferPath.value = idx > 0 ? cur.slice(0, idx) : '/sdcard';
    refreshTransferList();
  });
}
if (els.btnTransferPush) {
  els.btnTransferPush.addEventListener('click', () => pushToDevice());
}
if (els.btnTransferPull) {
  els.btnTransferPull.addEventListener('click', () => pullFromDevice());
}
if (els.transferLibrary) {
  els.transferLibrary.querySelectorAll('[data-lib]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (transferBusy) return;
      const id = btn.getAttribute('data-lib') || 'downloads';
      const lib = MEDIA_LIBRARY[id] || MEDIA_LIBRARY.downloads;
      transferLibraryId = id;
      transferCategory = lib.category || 'all';
      els.transferPath.value = lib.path;
      syncTransferLibraryButtons();
      syncTransferCatButtons();
      clearTransferPreview(
        `Biblioteca · ${lib.label}`,
        'Selecione um arquivo para ver o preview'
      );
      refreshTransferList();
    });
  });
  syncTransferLibraryButtons();
}
if (els.transferCats) {
  els.transferCats.querySelectorAll('[data-cat]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (transferBusy) return;
      transferCategory = btn.getAttribute('data-cat') || 'all';
      syncTransferCatButtons();
      renderTransferList(transferEntries, transferCurrentPath);
    });
  });
}
if (els.transferViews) {
  els.transferViews.querySelectorAll('[data-view]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (transferBusy) return;
      setTransferViewMode(btn.getAttribute('data-view') || 'list');
    });
  });
  syncTransferViewButtons();
}
if (els.transferPath) {
  els.transferPath.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      refreshTransferList();
    }
  });
}

// Auto-refresh leve ao abrir
setTimeout(() => {
  if (window.api.transferList) refreshTransferList();
}, 1200);

/** --- Automações --- */
let automationBusy = false;

function setAutomationBusy(busy) {
  automationBusy = !!busy;
  if (els.btnAutoJean15) els.btnAutoJean15.disabled = automationBusy;
  if (els.btnAutoStop) els.btnAutoStop.disabled = !automationBusy;
}

function setAutomationHint(text) {
  if (els.automationHint) els.automationHint.textContent = text || '';
}

if (window.api.onAutomationProgress) {
  window.api.onAutomationProgress((payload = {}) => {
    if (payload.message) setAutomationHint(payload.message);
    if (payload.done) setAutomationBusy(false);
  });
}

if (els.btnAutoJean15) {
  els.btnAutoJean15.addEventListener('click', async () => {
    if (automationBusy || !window.api.runAutomation) return;
    const sceneId = els.btnAutoJean15.getAttribute('data-scene') || 'bible-jean15-s21';
    setAutomationBusy(true);
    setAutomationHint('Iniciando cena…');
    try {
      const result = await window.api.runAutomation(sceneId);
      if (result?.message) setAutomationHint(result.message);
      if (!result?.success && !result?.cancelled) {
        setAutomationHint(result?.message || 'Falha na automação.');
      }
    } catch (err) {
      setAutomationHint(err?.message || 'Falha na automação.');
    } finally {
      setAutomationBusy(false);
    }
  });
}

if (els.btnAutoStop) {
  els.btnAutoStop.addEventListener('click', async () => {
    if (!window.api.stopAutomation) return;
    setAutomationHint('Parando…');
    try {
      const result = await window.api.stopAutomation();
      if (result?.message) setAutomationHint(result.message);
    } catch (err) {
      setAutomationHint(err?.message || 'Não foi possível parar.');
    }
  });
}
