"use strict";

(() => {
  const TOKEN = document.querySelector('meta[name="ultron-token"]').content;
  const $ = (id) => document.getElementById(id);
  const ui = {
    chat: $("chat"), input: $("input"), form: $("input-form"), send: $("send-btn"),
    mic: $("mic-btn"), mute: $("mute-btn"), stop: $("stop-btn"), clear: $("clear-btn"),
    continuous: $("continuous"), dot: $("status-dot"), state: $("status-state"), detail: $("status-detail"),
    settingsBtn: $("settings-btn"), dialog: $("settings"),
  };

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  const PREFS_KEY = "ultron.voicePrefs";

  const state = {
    prefs: { rate: 175, volume: 0.9, voice: "", language: "en-US", muted: false, continuous: false },
    recognition: null,
    listening: false,
    continuousSession: false,
    busy: false,
    stopped: false,
    aiProvider: "openai",
    aiModel: "",
    aiConfigured: false,
  };

  // ---- helpers -------------------------------------------------------------------

  async function api(path, body) {
    const options = { method: body ? "POST" : "GET", headers: { "X-Ultron-Token": TOKEN } };
    if (body) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  function setStatus(name, detail = "") {
    ui.dot.className = "dot " + name.toLowerCase();
    ui.state.textContent = name;
    ui.detail.textContent = detail ? "— " + detail : "";
  }

  function addMessage(role, text) {
    const wrapper = document.createElement("div");
    wrapper.className = "msg " + role;
    const meta = document.createElement("div");
    meta.className = "meta";
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    meta.textContent = (role === "user" ? "You" : "Ultron") + " · " + time;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text; // never innerHTML: replies are untrusted
    wrapper.append(meta, bubble);
    ui.chat.append(wrapper);
    ui.chat.scrollTop = ui.chat.scrollHeight;
  }

  function loadPrefs() {
    try { Object.assign(state.prefs, JSON.parse(localStorage.getItem(PREFS_KEY) || "{}")); } catch (_) { /* ignore */ }
  }

  function savePrefs() {
    localStorage.setItem(PREFS_KEY, JSON.stringify(state.prefs));
  }

  function readyStatus() {
    if (state.listening) setStatus("Listening", "speak now");
    else setStatus("Ready");
  }

  // ---- speech output -----------------------------------------------------------------

  function findVoice(wanted) {
    if (!synth || !wanted) return null;
    const lower = wanted.toLowerCase();
    return synth.getVoices().find((v) => v.voiceURI === wanted || v.name.toLowerCase().includes(lower)) || null;
  }

  function speak(text) {
    if (!text || state.prefs.muted || !synth) return Promise.resolve();
    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = Math.min(2, Math.max(0.5, state.prefs.rate / 175));
      utterance.volume = state.prefs.volume;
      const voice = findVoice(state.prefs.voice);
      if (voice) utterance.voice = voice;
      utterance.onend = resolve;
      utterance.onerror = resolve;
      setStatus("Speaking", "press Esc or Stop Speaking to interrupt");
      synth.cancel();
      synth.speak(utterance);
    });
  }

  function stopSpeaking() {
    if (synth) synth.cancel();
  }

  // ---- conversation flow --------------------------------------------------------------

  async function sendText(text, source = "typed") {
    text = (text || "").trim();
    if (!text || state.stopped) return;
    stopSpeaking();
    addMessage("user", text);
    setStatus("Processing", "Thinking…");
    state.busy = true;
    let reply;
    try {
      reply = await api("/api/message", { text });
    } catch (err) {
      state.busy = false;
      addMessage("error", "I can't reach Ultron's server. Make sure 'python main.py --web' is still running. (" + err.message + ")");
      setStatus("Error", "server unreachable");
      state.continuousSession = false;
      return;
    }
    state.busy = false;

    if (reply.action === "clear") ui.chat.replaceChildren();
    if (reply.text) addMessage(reply.is_error ? "error" : "assistant", reply.text);
    if (reply.action === "stop_speaking") stopSpeaking();

    await speak(reply.speech);

    if (reply.action === "exit") {
      shutDownPage();
      return;
    }
    if (reply.is_error) setStatus("Error", "AI unavailable — local commands still work");
    else readyStatus();
    if (state.continuousSession && source === "voice") startListening();
  }

  function shutDownPage() {
    state.stopped = true;
    state.continuousSession = false;
    stopListening();
    for (const el of [ui.input, ui.send, ui.mic, ui.mute, ui.stop, ui.clear, ui.continuous, ui.settingsBtn]) {
      el.disabled = true;
    }
    addMessage("system", "Ultron has shut down. You can close this tab. Run 'python main.py --web' to start again.");
    setStatus("Error", "Ultron is offline");
  }

  // ---- speech input -------------------------------------------------------------------

  const RECOGNITION_ERRORS = {
    "no-speech": ["system", "I didn't hear anything. Try again when you're ready."],
    "audio-capture": ["error", "No microphone was found. Connect one and check your system's sound settings."],
    "not-allowed": ["error", "Microphone access was blocked. Click the icon in the address bar to allow it, then try again."],
    "service-not-allowed": ["error", "Your browser blocked speech recognition. Try Chrome or Edge, or type instead."],
    "network": ["error", "The speech recognition service is unreachable. Check your internet connection or type instead."],
    "language-not-supported": ["error", "That recognition language isn't supported. Change it in Settings."],
  };

  function setMicUi(listening) {
    ui.mic.classList.toggle("listening", listening);
    ui.mic.textContent = listening ? "■ Stop Listening" : "● Start Listening";
  }

  function startListening() {
    if (!Recognition || state.listening || state.stopped) return;
    stopSpeaking();
    const recognition = new Recognition();
    recognition.lang = state.prefs.language || "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;
    let transcript = "";
    let fatal = false;

    recognition.onstart = () => {
      state.listening = true;
      setMicUi(true);
      setStatus("Listening", "speak now");
    };
    recognition.onresult = (event) => {
      transcript = Array.from(event.results).map((r) => r[0].transcript).join(" ").trim();
    };
    recognition.onerror = (event) => {
      if (event.error === "aborted") return;
      const [role, message] = RECOGNITION_ERRORS[event.error] || ["error", "Voice input failed (" + event.error + ")."];
      fatal = role === "error";
      if (!(event.error === "no-speech" && state.continuousSession)) addMessage(role, message);
    };
    recognition.onend = () => {
      state.listening = false;
      state.recognition = null;
      setMicUi(false);
      if (fatal) {
        state.continuousSession = false;
        setStatus("Error", "voice input problem — you can type instead");
      } else if (transcript) {
        sendText(transcript, "voice");
      } else if (state.continuousSession && !state.stopped) {
        startListening();
      } else {
        readyStatus();
      }
    };

    state.recognition = recognition;
    try {
      recognition.start();
    } catch (err) {
      state.recognition = null;
      addMessage("error", "Couldn't start the microphone: " + err.message);
    }
  }

  function stopListening() {
    state.continuousSession = false;
    if (state.recognition) state.recognition.stop();
  }

  // ---- settings ---------------------------------------------------------------------

  function populateVoices() {
    if (!synth) return;
    const select = $("set-voice");
    const current = state.prefs.voice;
    select.replaceChildren(new Option("System default", ""));
    for (const voice of synth.getVoices()) {
      select.append(new Option(`${voice.name} (${voice.lang})`, voice.voiceURI));
    }
    const match = findVoice(current);
    select.value = match ? match.voiceURI : "";
  }

  function openSettings() {
    $("set-provider").value = state.aiProvider;
    $("set-model").value = state.aiModel;
    $("set-key").value = "";
    $("key-hint").textContent = state.aiConfigured
      ? "A key is set. Leave blank to keep it."
      : "No key set. Paste your API key to enable AI answers.";
    $("set-rate").value = state.prefs.rate;
    $("set-volume").value = state.prefs.volume;
    $("set-lang").value = state.prefs.language;
    $("settings-error").textContent = "";
    populateVoices();
    updateRangeLabels();
    ui.dialog.showModal();
  }

  function updateRangeLabels() {
    $("rate-value").textContent = $("set-rate").value + " words/min";
    $("volume-value").textContent = Math.round($("set-volume").value * 100) + "%";
  }

  async function applySettings(save) {
    Object.assign(state.prefs, {
      voice: $("set-voice").value,
      rate: Number($("set-rate").value),
      volume: Number($("set-volume").value),
      language: $("set-lang").value.trim() || "en-US",
    });
    savePrefs();
    try {
      const result = await api("/api/settings", {
        ai_provider: $("set-provider").value,
        ai_model: $("set-model").value.trim(),
        api_key: $("set-key").value.trim(),
        save,
      });
      applyStatus(result);
      ui.dialog.close();
      addMessage("system", save ? "Settings saved to .env." : "Settings applied for this session.");
      if (result.ai_problem) addMessage("system", result.ai_problem);
    } catch (err) {
      $("settings-error").textContent = err.message;
    }
  }

  function applyStatus(status) {
    state.aiProvider = status.ai_provider;
    state.aiModel = status.ai_model;
    state.aiConfigured = status.ai_configured;
    $("version").textContent = "v" + status.version;
  }

  // ---- wiring -----------------------------------------------------------------------

  function refreshMuteButton() {
    ui.mute.textContent = state.prefs.muted ? "Unmute" : "Mute";
  }

  ui.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = ui.input.value;
    ui.input.value = "";
    sendText(text, "typed");
  });
  ui.mic.addEventListener("click", () => {
    if (state.listening) {
      stopListening();
    } else {
      state.continuousSession = ui.continuous.checked;
      startListening();
    }
  });
  ui.mute.addEventListener("click", () => {
    state.prefs.muted = !state.prefs.muted;
    if (state.prefs.muted) stopSpeaking();
    savePrefs();
    refreshMuteButton();
  });
  ui.stop.addEventListener("click", stopSpeaking);
  ui.clear.addEventListener("click", async () => {
    stopSpeaking();
    try { await api("/api/clear", {}); } catch (_) { /* the UI still clears */ }
    ui.chat.replaceChildren();
    addMessage("system", "Conversation cleared.");
  });
  ui.continuous.addEventListener("change", () => {
    state.prefs.continuous = ui.continuous.checked;
    state.continuousSession = state.listening && ui.continuous.checked;
    savePrefs();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !ui.dialog.open) stopSpeaking();
  });
  ui.settingsBtn.addEventListener("click", openSettings);
  $("settings-cancel").addEventListener("click", () => ui.dialog.close());
  $("settings-apply").addEventListener("click", () => applySettings(false));
  $("settings-save").addEventListener("click", () => applySettings(true));
  $("set-rate").addEventListener("input", updateRangeLabels);
  $("set-volume").addEventListener("input", updateRangeLabels);
  if (synth) synth.onvoiceschanged = () => { if (ui.dialog.open) populateVoices(); };
  window.addEventListener("beforeunload", () => { stopListening(); stopSpeaking(); });

  // ---- startup ------------------------------------------------------------------------

  async function init() {
    addMessage("assistant",
      "Hello, I'm Ultron, your personal AI voice assistant. Type a message below or press Start Listening " +
      "and speak. Say or type 'help' to see what I can do.");
    try {
      const status = await api("/api/status");
      applyStatus(status);
      const hadPrefs = localStorage.getItem(PREFS_KEY) !== null;
      if (!hadPrefs) Object.assign(state.prefs, status.voice);
      loadPrefs();
      if (status.ai_problem) addMessage("system", status.ai_problem);
    } catch (err) {
      addMessage("error", "Couldn't connect to Ultron's server: " + err.message);
      setStatus("Error", "server unreachable");
    }
    ui.continuous.checked = !!state.prefs.continuous;
    refreshMuteButton();
    if (!Recognition) {
      ui.mic.disabled = true;
      ui.mic.title = "Voice input needs Chrome or Edge";
      addMessage("system", "Voice input isn't supported in this browser. Use Chrome or Edge, or type instead.");
    }
    if (!synth) addMessage("system", "Spoken replies aren't supported in this browser; replies will appear on screen.");
    ui.input.focus();
  }

  loadPrefs();
  init();
})();
