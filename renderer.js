const btnStart = document.getElementById('btnStart');
const btnStop = document.getElementById('btnStop');
const btnFolder = document.getElementById('btnFolder');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const filePath = document.getElementById('filePath');

let isRecording = false;

// Eventos do main process
window.api.onRecordingStarted((data) => {
  setRecordingState(true, data.videoPath);
});

window.api.onRecordingStopped(() => {
  setRecordingState(false);
});

window.api.onRecordingError((msg) => {
  alert('Erro: ' + msg);
  setRecordingState(false);
});

// Botões
btnStart.addEventListener('click', async () => {
  btnStart.disabled = true;
  btnStart.textContent = 'Iniciando...';

  const result = await window.api.startRecording();

  if (result.success) {
    setRecordingState(true, result.videoPath);
  } else {
    alert(result.message);
    btnStart.disabled = false;
    btnStart.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10"/>
        <polygon points="10 8 16 12 10 16 10 8" fill="currentColor"/>
      </svg>
      INICIAR GRAVAÇÃO
    `;
  }
});

btnStop.addEventListener('click', async () => {
  btnStop.disabled = true;
  btnStop.textContent = 'Parando...';

  const result = await window.api.stopRecording();
  
  if (result.success) {
    setRecordingState(false);
    if (result.videoPath) {
      filePath.textContent = 'Arquivo salvo em:\n' + result.videoPath;
    }
  } else {
    alert(result.message);
  }

  btnStop.disabled = false;
});

btnFolder.addEventListener('click', async () => {
  const path = await window.api.chooseFolder();
  if (path) {
    filePath.textContent = 'Próximo arquivo:\n' + path;
  }
});

function setRecordingState(recording, path = null) {
  isRecording = recording;

  if (recording) {
    statusDot.classList.add('recording');
    statusText.textContent = 'GRAVANDO...';
    btnStart.style.display = 'none';
    btnStop.classList.add('visible');
    btnStop.disabled = false;
    btnStop.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="6" width="12" height="12" rx="1"/>
      </svg>
      PARAR GRAVAÇÃO
    `;
    if (path) {
      filePath.textContent = path;
    }
  } else {
    statusDot.classList.remove('recording');
    statusText.textContent = 'Pronto para gravar';
    btnStart.style.display = 'flex';
    btnStart.disabled = false;
    btnStart.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10"/>
        <polygon points="10 8 16 12 10 16 10 8" fill="currentColor"/>
      </svg>
      INICIAR GRAVAÇÃO
    `;
    btnStop.classList.remove('visible');
  }
}

// Status inicial
window.api.getStatus().then(status => {
  if (status.isRecording) {
    setRecordingState(true, status.videoPath);
  }
});
