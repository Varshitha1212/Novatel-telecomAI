import { Room, RoomEvent, Track, createLocalAudioTrack } from "https://esm.sh/livekit-client@2";
const API_BASE_URL = "https://novatel-telecomai-qtmt.onrender.com";
const conversation = document.querySelector("#conversation");
const form = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question");
const sendButton = document.querySelector("#send-button");
const voiceButton = document.querySelector("#voice-button");
const dictationButton = document.querySelector("#dictation-button");
const dictationIndicator = document.querySelector("#dictation-indicator");
const dictationIndicatorText = document.querySelector("#dictation-indicator-text");
const voiceStatus = document.querySelector("#voice-status");
const remoteAudio = document.querySelector("#remote-audio");
const callOverlay = document.querySelector("#call-overlay");
const callStatus = document.querySelector("#call-status");
const callAvatar = document.querySelector("#call-avatar");
const callHint = document.querySelector("#call-hint");
const callWaveform = document.querySelector("#call-waveform");
const endCallButton = document.querySelector("#end-call-button");

let room;
let microphone;
let crystalizedBallLoader;
let crystalizedBallCleanup;
let dictationStream;
let dictationRecorder;
let dictationChunks = [];
let sending = false;
let chatEnabled = false;
let speechSynthesisMessage;
let callAttempt = 0;

async function startCallVisual(attempt) {
  try {
    if (!crystalizedBallLoader) {
      crystalizedBallLoader = import("./crystalized-ball.js");
    }
    const { mountCrystalizedBall } = await crystalizedBallLoader;
    if (attempt !== callAttempt || callOverlay.hidden) return;
    crystalizedBallCleanup = mountCrystalizedBall(callAvatar);
    if (crystalizedBallCleanup) {
      callAvatar.classList.add("has-crystalized-ball");
    }
  } catch (error) {
    console.error("Could not load the live-call visual:", error);
    callHint.textContent = "Animated visual unavailable; your call can continue normally.";
  }
}

function stopCallVisual() {
  crystalizedBallCleanup?.();
  crystalizedBallCleanup = undefined;
  callAvatar.classList.remove("has-crystalized-ball");
  callHint.textContent = "Speak naturally. Nova will listen and respond.";
}

function addMessage(text, { fromUser = false, source = null, typing = false } = {}) {
  const message = document.createElement("div");
  message.className = `message ${fromUser ? "user-message" : "assistant-message"}${typing ? " typing" : ""}`;

  if (!fromUser) {
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "✳";
    message.append(avatar);
  }

  const content = document.createElement("div");
  content.className = "message-content";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const name = document.createElement("strong");
  name.textContent = fromUser ? "You" : "Nova assistant";
  const time = document.createElement("span");
  time.textContent = "Just now";
  meta.append(name, time);

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  content.append(meta, bubble);

  if (source) {
    const citation = document.createElement("span");
    citation.className = "source-note";
    citation.textContent = `Source: ${source}`;
    bubble.append(document.createElement("br"), citation);
  }

  message.append(content);
  if (!fromUser && !typing) {
    addReadAloudControl(content, text);
  }
  conversation.append(message);
  message.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return message;
}

function addReadAloudControl(content, text) {
  const readAloud = document.createElement("button");
  readAloud.className = "read-aloud-button";
  readAloud.type = "button";
  readAloud.setAttribute("aria-label", "Read response aloud");
  readAloud.title = "Read response aloud";
  readAloud.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4h4l5 4V6l-5 4H4Z"/><path d="M17 9a5 5 0 0 1 0 6m3-9a9 9 0 0 1 0 12"/></svg>';
  readAloud.addEventListener("click", () => readMessageAloud(readAloud, text));
  content.append(readAloud);
}

const greeting = conversation.querySelector(".assistant-message");
if (greeting) {
  addReadAloudControl(greeting.querySelector(".message-content"), greeting.querySelector(".message-bubble").textContent);
}

function readMessageAloud(button, text) {
  if (!("speechSynthesis" in window)) {
    voiceStatus.textContent = "Read-aloud isn’t supported in this browser.";
    return;
  }
  if (speechSynthesis.speaking) {
    speechSynthesis.cancel();
    document.querySelectorAll(".read-aloud-button[aria-pressed=true]").forEach((activeButton) => {
      activeButton.setAttribute("aria-pressed", "false");
      activeButton.title = "Read response aloud";
    });
    if (speechSynthesisMessage === button) {
      speechSynthesisMessage = undefined;
      return;
    }
  }

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-IN";
  utterance.onstart = () => {
    speechSynthesisMessage = button;
    button.setAttribute("aria-pressed", "true");
    button.title = "Stop reading";
  };
  utterance.onend = utterance.onerror = () => {
    button.setAttribute("aria-pressed", "false");
    button.title = "Read response aloud";
    if (speechSynthesisMessage === button) speechSynthesisMessage = undefined;
  };
  speechSynthesis.speak(utterance);
}

function describeRequestError(error) {
  if (error instanceof TypeError) {
    return "Can’t reach the support server. Make sure the app is running, then refresh this page.";
  }
  return error.message || "Something went wrong. Please try again.";
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    throw new Error(`The support server returned an unreadable response (HTTP ${response.status}).`);
  }
}

async function askQuestion(value) {
  const question = value.trim();
  if (!question || sending) return;
  if (!chatEnabled) {
    addMessage("Add OPENAI_API_KEY or GROQ_API_KEY to .env, then restart the app.");
    return;
  }

  sending = true;
  sendButton.disabled = true;
  addMessage(question, { fromUser: true });
  const pending = addMessage("Looking through the support knowledge base…", { typing: true });
  questionInput.value = "";

  try {
    const response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const result = await responseJson(response);
    if (!response.ok) throw new Error(result.detail || "Your question could not be sent.");
    pending.remove();
    addMessage(result.answer, { source: result.source });
  } catch (error) {
    pending.remove();
    addMessage(describeRequestError(error));
  } finally {
    sending = false;
    sendButton.disabled = false;
    questionInput.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  askQuestion(questionInput.value);
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => askQuestion(button.dataset.question));
});

function stopVoice() {
  callAttempt += 1;
  stopCallVisual();
  if (microphone) {
    microphone.stop();
    microphone = undefined;
  }
  const activeRoom = room;
  room = undefined;
  if (activeRoom) {
    activeRoom.disconnect();
  }
  remoteAudio.srcObject = null;
  voiceButton.classList.remove("listening");
  voiceButton.setAttribute("aria-label", "Start a live voice call");
  voiceButton.title = "Start a live voice call";
  callOverlay.hidden = true;
  callOverlay.setAttribute("aria-hidden", "true");
  callAvatar.classList.remove("speaking", "listening");
  callWaveform.classList.remove("active");
  voiceStatus.textContent = "Mic to dictate · phone for a live call · speaker to hear replies.";
}

function setCallActivity(activity) {
  const listening = activity === "Listening…" || activity === "You’re speaking…";
  const speaking = activity === "Nova is speaking…";
  callStatus.textContent = activity === "Listening…"
    ? "I’m listening — speak now"
    : activity === "You’re speaking…"
      ? "I hear you — keep speaking"
      : activity;
  callStatus.classList.toggle("is-listening", listening);
  callStatus.classList.toggle("is-speaking", speaking);
  callAvatar.classList.toggle("speaking", speaking);
  callAvatar.classList.toggle(
    "listening",
    listening,
  );
  callWaveform.classList.toggle("active", activity !== "Connecting to voice support…");
}

function stopDictation() {
  if (dictationRecorder?.state === "recording") {
    dictationRecorder.stop();
  }
}

async function startDictation() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    voiceStatus.textContent = "Audio recording isn’t supported in this browser. Try the live call or use Edge or Chrome.";
    return;
  }

  try {
    dictationStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    const message = error.name === "NotAllowedError" || error.name === "SecurityError"
      ? "Microphone access is blocked. Allow microphone access for this site, refresh, and try again."
      : error.name === "NotFoundError"
        ? "No microphone was found. Connect or enable a microphone, then try again."
        : error.name === "NotReadableError"
          ? "The microphone is busy in another app. Close that app and try again."
          : "Could not start the microphone. Check browser permissions and try again.";
    voiceStatus.textContent = message;
    return;
  }

  const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
    .find((type) => MediaRecorder.isTypeSupported(type));
  dictationChunks = [];
  try {
    dictationRecorder = mimeType
      ? new MediaRecorder(dictationStream, { mimeType })
      : new MediaRecorder(dictationStream);
  } catch {
    dictationStream.getTracks().forEach((track) => track.stop());
    dictationStream = undefined;
    voiceStatus.textContent = "Could not start audio recording in this browser. Try the live call or use Edge or Chrome.";
    return;
  }
  dictationRecorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) dictationChunks.push(event.data);
  });
  dictationRecorder.addEventListener("stop", async () => {
    const recorder = dictationRecorder;
    const audio = new Blob(dictationChunks, { type: recorder.mimeType || "audio/webm" });
    dictationRecorder = undefined;
    dictationChunks = [];
    dictationStream?.getTracks().forEach((track) => track.stop());
    dictationStream = undefined;
    dictationButton.classList.remove("listening");
    dictationButton.setAttribute("aria-label", "Dictate a message");
    dictationButton.setAttribute("aria-pressed", "false");
    dictationIndicator.hidden = true;

    if (!audio.size) {
      voiceStatus.textContent = "No audio was recorded. Check your microphone, then try again.";
      return;
    }

    voiceStatus.textContent = "Transcribing your message…";
    dictationButton.disabled = true;
    try {
      const response = await fetch(`${API_BASE_URL}/api/transcribe`, {
        method: "POST",
        headers: { "Content-Type": audio.type.split(";", 1)[0] || "audio/webm" },
        body: audio,
      });
      const result = await responseJson(response);
      if (!response.ok) throw new Error(result.detail || "Speech-to-text failed. Please try again.");
      const transcript = result.text?.trim();
      if (!transcript) {
        voiceStatus.textContent = "No words were recognized. Try speaking closer to your microphone.";
        return;
      }
      questionInput.value = [questionInput.value.trim(), transcript].filter(Boolean).join(" ");
      voiceStatus.textContent = "I got that — review your message, then send.";
      questionInput.focus();
    } catch (error) {
      voiceStatus.textContent = describeRequestError(error);
    } finally {
      dictationButton.disabled = false;
    }
  }, { once: true });

  dictationRecorder.start();
  dictationButton.classList.add("listening");
  dictationButton.setAttribute("aria-label", "Stop recording");
  dictationButton.setAttribute("aria-pressed", "true");
  dictationIndicator.hidden = false;
  dictationIndicatorText.textContent = "I’m listening — speak now, then tap the mic to finish";
  voiceStatus.textContent = "I’m listening — speak now, then tap the mic to finish.";
}

dictationButton.addEventListener("click", async () => {
  if (dictationRecorder?.state === "recording") {
    stopDictation();
    return;
  }
  dictationButton.disabled = true;
  try {
    await startDictation();
  } finally {
    dictationButton.disabled = false;
  }
});

voiceButton.addEventListener("click", async () => {
  if (room) {
    stopVoice();
    return;
  }

  const currentAttempt = ++callAttempt;
  voiceButton.disabled = true;
  voiceStatus.textContent = "Connecting to voice support…";
  callOverlay.hidden = false;
  callOverlay.setAttribute("aria-hidden", "false");
  setCallActivity("Connecting to voice support…");
  startCallVisual(currentAttempt);
  try {
   const response = await fetch(`${API_BASE_URL}/api/livekit/token`, { method: "POST" });
    const result = await responseJson(response);
    if (!response.ok) throw new Error(result.detail || "Voice support is unavailable.");
    if (currentAttempt !== callAttempt) return;

    const activeRoom = new Room();
    room = activeRoom;
    activeRoom.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) track.attach(remoteAudio);
    });
    activeRoom.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      if (room !== activeRoom) return;
      const localSpeaking = speakers.some(
        (participant) => participant.identity === activeRoom.localParticipant.identity,
      );
      const assistantSpeaking = speakers.some(
        (participant) => participant.identity !== activeRoom.localParticipant.identity,
      );
      setCallActivity(
        assistantSpeaking ? "Nova is speaking…" : localSpeaking ? "You’re speaking…" : "Listening…",
      );
    });
    activeRoom.on(RoomEvent.Disconnected, stopVoice);
    await activeRoom.connect(result.url, result.token);
    if (currentAttempt !== callAttempt) {
      await activeRoom.disconnect();
      return;
    }
    microphone = await createLocalAudioTrack();
    if (currentAttempt !== callAttempt) {
      microphone.stop();
      microphone = undefined;
      await activeRoom.disconnect();
      return;
    }
    await activeRoom.localParticipant.publishTrack(microphone);

    voiceButton.classList.add("listening");
    voiceButton.setAttribute("aria-label", "End live voice call");
    voiceButton.title = "End live voice call";
    voiceStatus.textContent = "Live voice call in progress.";
    setCallActivity("Listening…");
  } catch (error) {
    stopVoice();
    addMessage(describeRequestError(error));
  } finally {
    voiceButton.disabled = false;
  }
});

endCallButton.addEventListener("click", stopVoice);

window.addEventListener("pagehide", () => {
  if (dictationRecorder?.state === "recording") dictationRecorder.stop();
  dictationStream?.getTracks().forEach((track) => track.stop());
  if (microphone) microphone.stop();
  if (room) room.disconnect();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
});

fetch(`${API_BASE_URL}/api/config`)
  .then((response) => response.json())
  .then(({ chat_enabled: chatIsEnabled, voice_enabled: voiceEnabled, dictation_enabled: dictationEnabled, support_contact: supportContact }) => {
    const supportLink = document.querySelector("#support-contact");
    if (supportContact) {
      if (supportContact.includes("@") && !supportContact.includes("://")) {
        supportLink.href = `mailto:${supportContact}`;
      } else {
        supportLink.href = supportContact.includes("://")
          ? supportContact
          : `https://${supportContact}`;
      }
    } else {
      supportLink.textContent = "Set official contact in .env";
      supportLink.href = "#";
      supportLink.addEventListener("click", (event) => event.preventDefault());
    }
    chatEnabled = chatIsEnabled;
    if (!chatEnabled) {
      sendButton.disabled = true;
      questionInput.disabled = true;
      questionInput.placeholder = "Configure OpenAI or Groq to enable support";
      voiceStatus.textContent = "Set OPENAI_API_KEY or GROQ_API_KEY in .env to enable the LangChain agent.";
    }
    if (!voiceEnabled) {
      voiceButton.disabled = true;
      voiceButton.title = "Add a model provider and LiveKit credentials to .env to enable voice support";
      if (chatEnabled) {
        voiceStatus.textContent = "Chat is ready. Add LiveKit credentials to enable live calls.";
      }
    }
    if (!dictationEnabled) {
      dictationButton.disabled = true;
      dictationButton.title = "Add Groq or OpenAI credentials in .env to enable speech-to-text";
    }
  })
  .catch(() => {
    chatEnabled = false;
    sendButton.disabled = true;
    questionInput.disabled = true;
    questionInput.placeholder = "Chat support configuration could not be checked";
    voiceButton.disabled = true;
    voiceStatus.textContent = "Support agent configuration could not be checked.";
  });
