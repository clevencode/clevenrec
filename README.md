# Scrcpy + OBS Recorder

**Painel GUI simples e moderno** que inicia e para simultaneamente:

- **Vídeo** → via **scrcpy** (gravação limpa sem áudio)
- **Áudio** → via **OBS Studio** (usando WebSocket)

Ideal para gravações de qualidade onde você quer o melhor de cada ferramenta.

---

## 🎯 Objetivo do projeto

Você pediu:  
> "usar a base de codigo de um gui já pronto para um novo projeto: gravação de audio via obs + gravação de vídeo via screen copy: resultado final: um painel gui com botão de iniciar e para: ao clicar: inicia screen copy video e obs audio simultaneamente"

Este projeto foi criado **do zero**, limpo e focado exatamente nisso.  
Inspirado na simplicidade e na arquitetura do Escrcpy / QtScrcpy, mas sem a complexidade desnecessária de multi-device, mapeamento de teclado etc.

---

## 📦 Pré-requisitos

1. **Node.js** 18+ (https://nodejs.org)
2. **scrcpy** instalado e no PATH  
   - Baixe: https://github.com/Genymobile/scrcpy/releases  
   - Ou use: `winget install Genymobile.scrcpy`
3. **OBS Studio** (versão 28 ou superior — WebSocket já vem embutido)
4. **Android** com Depuração USB ativada

### Configurar OBS WebSocket (muito importante)

1. Abra o OBS
2. Vá em **Tools → WebSocket Server Settings**
3. Marque **Enable WebSocket server**
4. Porta padrão: **4455**
5. (Opcional) Defina uma senha — se definir, altere no `main.js`
6. Clique em **OK** e reinicie o OBS se necessário

---

## 🚀 Como rodar (desenvolvimento)

```bash
# 1. Entre na pasta
cd scrcpy-obs-recorder

# 2. Instale as dependências
npm install

# 3. Rode
npm start
```

---

## 🏗️ Estrutura do projeto (simples e clara)

```
scrcpy-obs-recorder/
├── main.js          ← Processo principal Electron + lógica de scrcpy + OBS
├── preload.js       ← Bridge segura entre main e renderer
├── index.html       ← Interface (HTML + CSS moderno)
├── renderer.js      ← Lógica dos botões
├── package.json
└── README.md
```

---

## ✨ Funcionalidades

- Botão **INICIAR** → inicia scrcpy (vídeo) + OBS (áudio) ao mesmo tempo
- Botão **PARAR** → para os dois
- Indicador visual pulsante enquanto grava
- Escolha de pasta de destino
- Interface limpa, dark mode, moderna (inspirada no design Apple / Escrcpy)
- Tratamento de erros (se scrcpy ou OBS falhar, avisa)

---

## 🔧 Personalizações fáceis

No arquivo `main.js` você pode alterar:

```js
const config = {
  scrcpyPath: 'scrcpy',                    // caminho completo se precisar
  // ...
  obsPassword: 'sua-senha-aqui',           // se você colocou senha no OBS
};
```

Flags do scrcpy (linha ~70):

```js
const scrcpyArgs = [
  '--no-audio',
  '--record', config.recordVideoPath,
  '--max-size', '1920',
  '--video-bit-rate', '8M',
  // '--stay-awake',
  // '--turn-screen-off',
  // '--max-fps', '60',
];
```

---

## 📌 Próximos passos possíveis (se quiser evoluir)

- Salvar configurações (path do scrcpy, senha OBS, pasta padrão)
- Selecionar dispositivo específico
- Gravar áudio do microfone + sistema no OBS
- Preview do scrcpy dentro do painel
- Timer de gravação
- Atalho de teclado global (Ctrl+Shift+R)

---

## 💡 Por que não usei o código do Escrcpy diretamente?

O Escrcpy é excelente, mas é um monorepo grande com Vue 3 + Pinia + vários módulos (copilot, file explorer, multi-device etc.).  
Para o seu objetivo específico (apenas Start/Stop de scrcpy + OBS), seria overkill e difícil de manter.

Este projeto é a **versão mínima e focada** que você pediu, usando a mesma ideia de arquitetura (Electron + main process controlando processos externos).

---

Criado especialmente para o Cleven 🔥  
Qualquer dúvida ou quiser que eu adicione mais features (timer, seleção de dispositivo, preview etc.), é só falar!
