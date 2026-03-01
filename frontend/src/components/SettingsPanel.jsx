import React, { useState, useEffect, useRef } from "react";
import {
  getSettings,
  updateSettings,
  testLMStudio,
  getVoices,
  updateVoices,
  getRoleLlmSettings,
  updateRoleLlmSettings,
  testVoice,
  downloadUrl,
  getMusicSettings,
  updateMusicSettings,
  listMusicFiles,
  uploadMusicFile,
  musicFileUrl,
  getPostprocessSettings,
  updatePostprocessSettings,
  getOcrSettings,
  updateOcrSettings,
  getVisionIngestSettings,
  updateVisionIngestSettings,
  getPronunciationOverrides,
  updatePronunciationOverrides,
  clearDatabase,
} from "../api/client";
import "./SettingsPanel.css";

const VOICE_WAKE_WORD_KEY = "voice-assistant-wake-word";
const VOICE_STT_MODEL_KEY = "voice-assistant-stt-model";
const VOICE_SETTINGS_EVENT = "voice-assistant-settings-changed";
const VOICE_STT_MODELS = ["tiny", "base", "small"];

function normalizeVoiceSttModel(raw) {
  const v = String(raw || "").trim().toLowerCase();
  return VOICE_STT_MODELS.includes(v) ? v : "small";
}

const DEFAULT_PARTICIPANTS = [
  { role: "host", name: "Игорь" },
  { role: "guest1", name: "Аня" },
  { role: "guest2", name: "Максим" },
];

function normalizeParticipants(list) {
  const raw = Array.isArray(list) ? list : [];
  const base = raw.length ? raw : DEFAULT_PARTICIPANTS;
  const usedRoles = new Set();
  const usedNames = new Set();
  const out = [];
  for (let i = 0; i < base.length; i += 1) {
    const row = base[i] || {};
    let role = String(row.role || "").trim() || DEFAULT_PARTICIPANTS[i]?.role || `role_${i + 1}`;
    let name = String(row.name || "").trim() || DEFAULT_PARTICIPANTS[i]?.name || `Спикер ${i + 1}`;
    let rn = 2;
    while (usedRoles.has(role.toLowerCase())) {
      role = `${role}_${rn}`;
      rn += 1;
    }
    let nn = 2;
    while (usedNames.has(name.toLowerCase())) {
      name = `${name} ${nn}`;
      nn += 1;
    }
    usedRoles.add(role.toLowerCase());
    usedNames.add(name.toLowerCase());
    out.push({ role, name });
  }
  return out.length ? out : DEFAULT_PARTICIPANTS;
}

function participantNames(list) {
  return normalizeParticipants(list).map((p) => p.name);
}

function normalizePronRowsFromOverrides(overridesObj) {
  const entries = Object.entries(overridesObj || {})
    .filter(([from, to]) => String(from).trim() && String(to).trim())
    .map(([from, to]) => ({ from: String(from), to: String(to) }));
  const keep = entries.slice(-3);
  const hidden = Object.fromEntries(entries.slice(0, Math.max(0, entries.length - 3)).map((r) => [r.from, r.to]));
  return { rows: [...keep, { from: "", to: "" }], hidden };
}

function normalizeRoleLlmMapForNames(namesList, roleLlmMap, fallbackBaseUrl = "") {
  const roles = (Array.isArray(namesList) ? namesList : [])
    .map((x) => String(x || "").trim())
    .filter(Boolean);
  const effectiveRoles = roles.length ? roles : participantNames(DEFAULT_PARTICIPANTS);
  const src = roleLlmMap && typeof roleLlmMap === "object" ? roleLlmMap : {};
  const out = {};
  for (const role of effectiveRoles) {
    const cfg = src[role] && typeof src[role] === "object" ? src[role] : {};
    out[role] = {
      model: String(cfg.model || ""),
      base_url: String(cfg.base_url || fallbackBaseUrl || ""),
    };
  }
  return out;
}

function compactRoleLlmMap(map, fallbackBaseUrl = "") {
  const out = {};
  const baseFallback = String(fallbackBaseUrl || "").trim();
  for (const [role, cfg] of Object.entries(map || {})) {
    const roleName = String(role || "").trim();
    if (!roleName || !cfg || typeof cfg !== "object") continue;
    const model = String(cfg.model || "").trim();
    const baseUrl = String(cfg.base_url || "").trim();
    if (!model) continue;
    out[roleName] = {
      model,
      ...(baseUrl && baseUrl !== baseFallback ? { base_url: baseUrl } : {}),
    };
  }
  return out;
}

export default function SettingsPanel({
  onClose,
  participants = DEFAULT_PARTICIPANTS,
  onParticipantsChange,
  roleLlmMap = {},
  onRoleLlmMapChange,
}) {
  const [settings, setSettings] = useState({
    base_url: "",
    model: "",
    temperature: 0.4,
    max_tokens: 4096,
  });
  const [voices, setVoices] = useState({ voices: {}, available: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingVoices, setSavingVoices] = useState(false);
  const [savedVoices, setSavedVoices] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saveVoicesError, setSaveVoicesError] = useState(null);
  const [voiceTestStatus, setVoiceTestStatus] = useState({});
  const [savingRoleLlm, setSavingRoleLlm] = useState(false);
  const [savedRoleLlm, setSavedRoleLlm] = useState(false);
  const [saveRoleLlmError, setSaveRoleLlmError] = useState(null);
  const [availableModels, setAvailableModels] = useState([]);
  const [pronRows, setPronRows] = useState([]);
  const [pronHiddenOverrides, setPronHiddenOverrides] = useState({});
  const [savingPron, setSavingPron] = useState(false);
  const [savedPron, setSavedPron] = useState(false);
  const [savePronError, setSavePronError] = useState(null);
  const [savedDbReset, setSavedDbReset] = useState(false);
  const [dbResetting, setDbResetting] = useState(false);
  const [dbResetError, setDbResetError] = useState(null);
  const [music, setMusic] = useState({
    enabled: false,
    assets_dir: "/opt/audio-assets",
    intro_file: "intro.mp3",
    background_file: "background.mp3",
    outro_file: "outro.mp3",
    intro_volume: 0.85,
    background_volume: 0.1,
    outro_volume: 0.9,
  });
  const [savingMusic, setSavingMusic] = useState(false);
  const [savedMusic, setSavedMusic] = useState(false);
  const [saveMusicError, setSaveMusicError] = useState(null);
  const [musicFiles, setMusicFiles] = useState([]);
  const [uploadingMusicSlot, setUploadingMusicSlot] = useState(null);
  const [musicPreviewName, setMusicPreviewName] = useState("");
  const [post, setPost] = useState({
    enabled: false,
    loudnorm: true,
    compressor: true,
    limiter: true,
    target_lufs: -16,
    true_peak_db: -1.5,
    lra: 11,
  });
  const [visionIngest, setVisionIngest] = useState({
    enabled: false,
    base_url: "http://localhost:1234",
    model: "",
    timeout_seconds: 60,
    max_images_per_document: 20,
  });
  const [savingVisionIngest, setSavingVisionIngest] = useState(false);
  const [savedVisionIngest, setSavedVisionIngest] = useState(false);
  const [saveVisionIngestError, setSaveVisionIngestError] = useState(null);
  const [ocr, setOcr] = useState({
    enabled: true,
    mode: "fast",
    lang: "rus+eng",
    min_chars: 8,
    max_pdf_pages: 40,
    max_docx_images: 40,
  });
  const [savingPost, setSavingPost] = useState(false);
  const [savedPost, setSavedPost] = useState(false);
  const [savePostError, setSavePostError] = useState(null);
  const [savingOcr, setSavingOcr] = useState(false);
  const [savedOcr, setSavedOcr] = useState(false);
  const [saveOcrError, setSaveOcrError] = useState(null);
  const [participantDraft, setParticipantDraft] = useState(() => normalizeParticipants(participants));
  const [roleLlmDraft, setRoleLlmDraft] = useState(() =>
    normalizeRoleLlmMapForNames(participantNames(participants), roleLlmMap, "")
  );
  const musicPreviewRef = useRef(null);
  const modelProbeBaseRef = useRef("");
  const [voiceWakeWord, setVoiceWakeWord] = useState(() => {
    try {
      return (localStorage.getItem(VOICE_WAKE_WORD_KEY) || "Гена").trim() || "Гена";
    } catch (_) {
      return "Гена";
    }
  });
  const [voiceWakeSaved, setVoiceWakeSaved] = useState(false);
  const [voiceSttModel, setVoiceSttModel] = useState(() => {
    try {
      return normalizeVoiceSttModel(localStorage.getItem(VOICE_STT_MODEL_KEY) || "small");
    } catch (_) {
      return "small";
    }
  });

  function isValidServerUrl(s) {
    const t = (s || "").trim();
    if (!t) return true;
    try {
      const u = new URL(t);
      return (u.protocol === "http:" || u.protocol === "https:") && u.host;
    } catch {
      return false;
    }
  }

  const temp = Number(settings.temperature);
  const maxTok = Number(settings.max_tokens);
  const settingsValid =
    isValidServerUrl(settings.base_url) &&
    !Number.isNaN(temp) && temp >= 0 && temp <= 2 &&
    !Number.isNaN(maxTok) && maxTok >= 256 && maxTok <= 32768;
  const appliedParticipants = normalizeParticipants(participants);
  const participantsSaveError = saveVoicesError || saveRoleLlmError;

  useEffect(() => {
    Promise.all([
      getSettings(),
      getVoices(),
      getRoleLlmSettings(),
      getPronunciationOverrides(),
      getMusicSettings(),
      getPostprocessSettings(),
      getOcrSettings(),
      listMusicFiles(),
      getVisionIngestSettings(),
    ])
      .then(([s, v, roleLlmRes, p, m, pp, ocrCfg, mf, visionCfg]) => {
        setSettings(s);
        setVoices(v);
        const loadedRoleLlmMap = (roleLlmRes && roleLlmRes.role_llm_map) || {};
        onRoleLlmMapChange?.(loadedRoleLlmMap);
        setRoleLlmDraft(normalizeRoleLlmMapForNames(participantNames(participants), loadedRoleLlmMap, s?.base_url || ""));
        const normPron = normalizePronRowsFromOverrides((p && p.overrides) || {});
        setPronRows(normPron.rows);
        setPronHiddenOverrides(normPron.hidden);
        if (m) setMusic(m);
        if (pp) setPost(pp);
        if (ocrCfg) setOcr(ocrCfg);
        setMusicFiles((mf && mf.files) || []);
        if (visionCfg) setVisionIngest(visionCfg);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    setParticipantDraft(normalizeParticipants(participants));
  }, [participants]);

  useEffect(() => {
    setRoleLlmDraft((prev) => {
      const normalized = normalizeRoleLlmMapForNames(participantNames(participants), roleLlmMap, settings.base_url || "");
      for (const role of Object.keys(normalized)) {
        if (prev && prev[role]) {
          normalized[role] = {
            model: String(normalized[role].model || prev[role].model || ""),
            base_url: String(normalized[role].base_url || prev[role].base_url || settings.base_url || ""),
          };
        }
      }
      return normalized;
    });
  }, [participants, roleLlmMap, settings.base_url]);

  useEffect(() => () => {
    if (musicPreviewRef.current) {
      try {
        musicPreviewRef.current.pause();
      } catch {}
      musicPreviewRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (loading) return;
    const base = (settings.base_url || "").trim();
    if (!base || !isValidServerUrl(base)) return;
    if (modelProbeBaseRef.current === base) return;
    modelProbeBaseRef.current = base;
    let cancelled = false;
    testLMStudio(base)
      .then((res) => {
        if (cancelled || !res || res.status !== "ok" || !Array.isArray(res.models)) return;
        setAvailableModels(res.models);
        setTestResult(res);
        setSettings((prev) => {
          if ((prev.base_url || "").trim() !== base) return prev;
          if (res.models.length === 0) return prev;
          if (prev.model && res.models.includes(prev.model)) return prev;
          return { ...prev, model: res.models[0] };
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [loading, settings.base_url]);

  async function handleSave() {
    if (!settingsValid) return;
    setSaveError(null);
    setSaving(true);
    setSaved(false);
    try {
      const updated = await updateSettings(settings);
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setSaveError(e.message || "Ошибка сохранения");
      setTimeout(() => setSaveError(null), 8000);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testLMStudio(settings.base_url);
      setTestResult(res);
      modelProbeBaseRef.current = (settings.base_url || "").trim();
      if (res && res.status === "ok" && Array.isArray(res.models)) {
        setAvailableModels(res.models);
        if (!settings.model && res.models.length > 0) {
          setSettings((prev) => ({ ...prev, model: res.models[0] }));
        }
      }
    } catch (e) {
      setTestResult({ status: "error", detail: e.message });
    } finally {
      setTesting(false);
    }
  }

  async function handleSaveVoices() {
    setSaveVoicesError(null);
    setSavingVoices(true);
    setSavedVoices(false);
    try {
      const updated = await updateVoices(voices);
      setVoices(updated);
      setSavedVoices(true);
      setTimeout(() => setSavedVoices(false), 3000);
    } catch (e) {
      setSaveVoicesError(e.message || "Ошибка сохранения голосов");
      setTimeout(() => setSaveVoicesError(null), 8000);
    } finally {
      setSavingVoices(false);
    }
  }

  function computeParticipantsPatch(nextDraftRows = participantDraft) {
    const prevParticipants = normalizeParticipants(participants);
    const nextParticipants = normalizeParticipants(nextDraftRows);
    const prevNames = prevParticipants.map((p) => p.name);
    const nextNames = nextParticipants.map((p) => p.name);
    const prevVoicesMap = voices.voices || {};

    const nextVoicesMap = {};
    for (let i = 0; i < nextNames.length; i += 1) {
      const nextName = nextNames[i];
      const prevName = prevNames[i];
      nextVoicesMap[nextName] = prevVoicesMap[nextName] || (prevName ? prevVoicesMap[prevName] : null) || { model: "", speaker: "0" };
    }

    const nextRoleLlmDraftMap = {};
    for (let i = 0; i < nextNames.length; i += 1) {
      const nextName = nextNames[i];
      const prevName = prevNames[i];
      nextRoleLlmDraftMap[nextName] = roleLlmDraft[nextName] || (prevName ? roleLlmDraft[prevName] : null) || {
        model: "",
        base_url: settings.base_url || "",
      };
    }
    const nextRoleLlmCompact = compactRoleLlmMap(nextRoleLlmDraftMap, settings.base_url || "");

    return {
      nextParticipants,
      nextVoicesState: { ...voices, voices: nextVoicesMap },
      nextRoleLlmDraftMap,
      nextRoleLlmCompact,
    };
  }

  function handleApplyParticipants() {
    const patch = computeParticipantsPatch(participantDraft);
    setParticipantDraft(patch.nextParticipants);
    onParticipantsChange?.(patch.nextParticipants);
    setVoices(patch.nextVoicesState);
    setRoleLlmDraft(patch.nextRoleLlmDraftMap);
    onRoleLlmMapChange?.(patch.nextRoleLlmCompact);
  }

  function handleApplyRoleLlm() {
    onRoleLlmMapChange?.(compactRoleLlmMap(roleLlmDraft, settings.base_url || ""));
  }

  async function handleSaveParticipantsAll() {
    setSaveVoicesError(null);
    setSaveRoleLlmError(null);
    setSavingVoices(true);
    setSavingRoleLlm(true);
    setSavedVoices(false);
    setSavedRoleLlm(false);
    try {
      const patch = computeParticipantsPatch(participantDraft);
      setParticipantDraft(patch.nextParticipants);
      onParticipantsChange?.(patch.nextParticipants);
      setVoices(patch.nextVoicesState);
      setRoleLlmDraft(patch.nextRoleLlmDraftMap);
      onRoleLlmMapChange?.(patch.nextRoleLlmCompact);

      const updatedVoices = await updateVoices(patch.nextVoicesState);
      setVoices(updatedVoices);

      const roleRes = await updateRoleLlmSettings({ role_llm_map: patch.nextRoleLlmCompact });
      const nextMap = (roleRes && roleRes.role_llm_map) || {};
      onRoleLlmMapChange?.(nextMap);
      setRoleLlmDraft(normalizeRoleLlmMapForNames(participantNames(patch.nextParticipants), nextMap, settings.base_url || ""));

      setSavedVoices(true);
      setSavedRoleLlm(true);
      setTimeout(() => setSavedVoices(false), 3000);
      setTimeout(() => setSavedRoleLlm(false), 3000);
    } catch (e) {
      const msg = e.message || "Не удалось сохранить участников";
      setSaveVoicesError(msg);
      setSaveRoleLlmError(msg);
      setTimeout(() => setSaveVoicesError(null), 8000);
      setTimeout(() => setSaveRoleLlmError(null), 8000);
    } finally {
      setSavingVoices(false);
      setSavingRoleLlm(false);
    }
  }

  async function handleSaveRoleLlm() {
    setSavingRoleLlm(true);
    setSavedRoleLlm(false);
    setSaveRoleLlmError(null);
    try {
      const payload = { role_llm_map: compactRoleLlmMap(roleLlmDraft, settings.base_url || "") };
      const res = await updateRoleLlmSettings(payload);
      const nextMap = (res && res.role_llm_map) || {};
      onRoleLlmMapChange?.(nextMap);
      setRoleLlmDraft(normalizeRoleLlmMapForNames(participantNames(participants), nextMap, settings.base_url || ""));
      setSavedRoleLlm(true);
      setTimeout(() => setSavedRoleLlm(false), 3000);
    } catch (e) {
      setSaveRoleLlmError(e.message || "Ошибка сохранения LLM по ролям");
      setTimeout(() => setSaveRoleLlmError(null), 8000);
    } finally {
      setSavingRoleLlm(false);
    }
  }

  async function handleTestVoice(slot) {
    const model = voices.voices && voices.voices[slot] && voices.voices[slot].model;
    if (!model) return;
    setVoiceTestStatus((prev) => ({ ...prev, [slot]: "testing" }));
    try {
      const res = await testVoice(slot);
      if (res && res.filename) {
        const audio = new Audio(downloadUrl(res.filename));
        audio.play().catch(() => {});
        setVoiceTestStatus((prev) => ({ ...prev, [slot]: "ok" }));
        setTimeout(() => {
          setVoiceTestStatus((prev) => ({ ...prev, [slot]: null }));
        }, 4000);
      } else {
        setVoiceTestStatus((prev) => ({ ...prev, [slot]: "error" }));
      }
    } catch (e) {
      setVoiceTestStatus((prev) => ({ ...prev, [slot]: "error" }));
    }
  }

  function setPronRow(index, patch) {
    setPronRows((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function addPronRow() {
    setPronRows((prev) => {
      const filled = prev.filter((r) => String(r.from || "").trim() && String(r.to || "").trim());
      let next = [...filled, { from: "", to: "" }];
      if (next.length > 4) {
        const overflow = next.slice(0, next.length - 4).filter((r) => String(r.from || "").trim() && String(r.to || "").trim());
        if (overflow.length > 0) {
          setPronHiddenOverrides((prevHidden) => {
            const merged = { ...prevHidden };
            for (const row of overflow) {
              merged[String(row.from).trim()] = String(row.to).trim();
            }
            return merged;
          });
        }
        next = next.slice(next.length - 4);
      }
      return next;
    });
  }

  function removePronRow(index) {
    setPronRows((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSavePron() {
    setSavingPron(true);
    setSavedPron(false);
    setSavePronError(null);
    try {
      const current = await getPronunciationOverrides();
      const currentOverrides = (current && current.overrides) || {};
      const overrides = { ...pronHiddenOverrides };
      for (const row of pronRows) {
        const from = (row.from || "").trim();
        const to = (row.to || "").trim();
        if (!from || !to) continue;
        overrides[from] = to;
      }
      const removed = Object.keys(currentOverrides).filter((k) => !(k in overrides));
      if (removed.length > 0) {
        const ok = window.confirm(
          `Внимание: будет удалено ${removed.length} существующих правил из словаря. Продолжить?`
        );
        if (!ok) {
          setSavingPron(false);
          return;
        }
      }
      const res = await updatePronunciationOverrides({ overrides });
      const normPron = normalizePronRowsFromOverrides((res && res.overrides) || {});
      setPronRows(normPron.rows);
      setPronHiddenOverrides(normPron.hidden);
      setSavedPron(true);
      setTimeout(() => setSavedPron(false), 3000);
    } catch (e) {
      setSavePronError(e.message || "Ошибка сохранения словаря");
      setTimeout(() => setSavePronError(null), 8000);
    } finally {
      setSavingPron(false);
    }
  }

  async function handleResetPron() {
    const ok = window.confirm("Сбросить весь словарь произношений? Это удалит все правила.");
    if (!ok) return;
    setSavingPron(true);
    setSavedPron(false);
    setSavePronError(null);
    try {
      const res = await updatePronunciationOverrides({ overrides: {} });
      const normPron = normalizePronRowsFromOverrides((res && res.overrides) || {});
      setPronRows(normPron.rows);
      setPronHiddenOverrides(normPron.hidden);
      setSavedPron(true);
      setTimeout(() => setSavedPron(false), 3000);
    } catch (e) {
      setSavePronError(e.message || "Ошибка сброса словаря");
      setTimeout(() => setSavePronError(null), 8000);
    } finally {
      setSavingPron(false);
    }
  }

  function pronRowsToOverrides(rows) {
    const overrides = {};
    for (const row of rows || []) {
      const from = String(row?.from || "").trim();
      const to = String(row?.to || "").trim();
      if (!from || !to) continue;
      overrides[from] = to;
    }
    return overrides;
  }

  function handleExportPron() {
    const overrides = { ...pronHiddenOverrides, ...pronRowsToOverrides(pronRows) };
    const payload = {
      exported_at: new Date().toISOString(),
      overrides,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "pronunciation_overrides.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImportPron(file) {
    if (!file) return;
    setSavePronError(null);
    try {
      const raw = await file.text();
      const parsed = JSON.parse(raw);
      const candidate = parsed && typeof parsed === "object" ? (parsed.overrides || parsed) : {};
      if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
        throw new Error("Ожидается JSON-объект формата {\"overrides\": {\"что\":\"как\"}}");
      }
      const cleaned = Object.fromEntries(
        Object.entries(candidate).filter(([k, v]) => String(k).trim() && String(v).trim())
      );
      const normPron = normalizePronRowsFromOverrides(cleaned);
      setPronRows(normPron.rows);
      setPronHiddenOverrides(normPron.hidden);
    } catch (e) {
      setSavePronError(e.message || "Ошибка импорта словаря");
      setTimeout(() => setSavePronError(null), 8000);
    }
  }

  async function handleResetDatabase() {
    const c1 = window.confirm(
      "Это удалит все документы, индексы, чаты, задания и сгенерированные файлы. Продолжить?"
    );
    if (!c1) return;
    const c2 = window.confirm(
      "Подтвердите еще раз: действие необратимо. Очистить базу полностью?"
    );
    if (!c2) return;

    setDbResetting(true);
    setSavedDbReset(false);
    setDbResetError(null);
    try {
      await clearDatabase({ confirm_step_1: true, confirm_step_2: true });
      setSavedDbReset(true);
      setTimeout(() => setSavedDbReset(false), 5000);
    } catch (e) {
      setDbResetError(e.message || "Ошибка очистки базы");
      setTimeout(() => setDbResetError(null), 8000);
    } finally {
      setDbResetting(false);
    }
  }

  async function handleSaveMusic() {
    setSavingMusic(true);
    setSavedMusic(false);
    setSaveMusicError(null);
    try {
      const updated = await updateMusicSettings(music);
      setMusic(updated);
      setSavedMusic(true);
      setTimeout(() => setSavedMusic(false), 3000);
    } catch (e) {
      setSaveMusicError(e.message || "Ошибка сохранения настроек музыки");
      setTimeout(() => setSaveMusicError(null), 8000);
    } finally {
      setSavingMusic(false);
    }
  }

  async function handleUploadMusic(slot, file) {
    if (!file) return;
    setUploadingMusicSlot(slot);
    setSaveMusicError(null);
    try {
      const res = await uploadMusicFile(slot, file);
      if (res && res.settings) setMusic(res.settings);
      const mf = await listMusicFiles();
      setMusicFiles((mf && mf.files) || []);
    } catch (e) {
      setSaveMusicError(e.message || "Ошибка загрузки музыкального файла");
      setTimeout(() => setSaveMusicError(null), 8000);
    } finally {
      setUploadingMusicSlot(null);
    }
  }

  function playMusicFile(filename) {
    if (!filename) return;
    if (musicPreviewRef.current) {
      try {
        musicPreviewRef.current.pause();
      } catch {}
      musicPreviewRef.current = null;
    }
    const audio = new Audio(musicFileUrl(filename));
    audio.onended = () => {
      setMusicPreviewName("");
      musicPreviewRef.current = null;
    };
    musicPreviewRef.current = audio;
    setMusicPreviewName(filename);
    audio.play().catch(() => {});
  }

  function stopMusicPreview() {
    if (!musicPreviewRef.current) return;
    try {
      musicPreviewRef.current.pause();
      musicPreviewRef.current.currentTime = 0;
    } catch {}
    musicPreviewRef.current = null;
    setMusicPreviewName("");
  }

  async function handleSavePost() {
    setSavingPost(true);
    setSavedPost(false);
    setSavePostError(null);
    try {
      const updated = await updatePostprocessSettings(post);
      setPost(updated);
      setSavedPost(true);
      setTimeout(() => setSavedPost(false), 3000);
    } catch (e) {
      setSavePostError(e.message || "Ошибка сохранения пост-обработки");
      setTimeout(() => setSavePostError(null), 8000);
    } finally {
      setSavingPost(false);
    }
  }

  async function handleSaveOcr() {
    setSavingOcr(true);
    setSavedOcr(false);
    setSaveOcrError(null);
    try {
      const updated = await updateOcrSettings(ocr);
      setOcr(updated);
      setSavedOcr(true);
      setTimeout(() => setSavedOcr(false), 3000);
    } catch (e) {
      setSaveOcrError(e.message || "Ошибка сохранения OCR-настроек");
      setTimeout(() => setSaveOcrError(null), 8000);
    } finally {
      setSavingOcr(false);
    }
  }

  async function handleSaveVisionIngest() {
    setSavingVisionIngest(true);
    setSavedVisionIngest(false);
    setSaveVisionIngestError(null);
    try {
      const updated = await updateVisionIngestSettings(visionIngest);
      setVisionIngest(updated);
      setSavedVisionIngest(true);
      setTimeout(() => setSavedVisionIngest(false), 3000);
    } catch (e) {
      setSaveVisionIngestError(e.message || "Ошибка сохранения настроек Vision ingest");
      setTimeout(() => setSaveVisionIngestError(null), 8000);
    } finally {
      setSavingVisionIngest(false);
    }
  }

  function handleSaveVoiceAssistantSettings() {
    const nextWake = String(voiceWakeWord || "").trim() || "Гена";
    const nextSttModel = normalizeVoiceSttModel(voiceSttModel);
    try {
      localStorage.setItem(VOICE_WAKE_WORD_KEY, nextWake);
      localStorage.setItem(VOICE_STT_MODEL_KEY, nextSttModel);
      window.dispatchEvent(new CustomEvent(VOICE_SETTINGS_EVENT));
      setVoiceWakeWord(nextWake);
      setVoiceSttModel(nextSttModel);
      setVoiceWakeSaved(true);
      setTimeout(() => setVoiceWakeSaved(false), 2500);
    } catch (e) {
      setSaveError(e?.message || "Не удалось сохранить настройки голосового ассистента");
      setTimeout(() => setSaveError(null), 8000);
    }
  }

  if (loading) {
    return <div className="card settings-panel"><p>Загрузка настроек…</p></div>;
  }

  return (
    <div className="card settings-panel">
      <div className="settings-header">
        <h3>Настройки LM Studio</h3>
        <button className="secondary close-btn" onClick={onClose} title="Закрыть панель настроек">Закрыть</button>
      </div>

      <div className="settings-form">
        <label title="Адрес API LM Studio (например http://localhost:1234/v1)">
          <span className="label-text">URL сервера</span>
          <input
            type="text"
            value={settings.base_url}
            onChange={(e) => setSettings({ ...settings, base_url: e.target.value })}
            placeholder="Например: http://10.55.12.228:1234/v1"
          />
        </label>

        <label title="Имя модели в LM Studio (должна быть загружена в сервер)">
          <span className="label-text">Модель</span>
          {availableModels.length > 0 ? (
            <select
              value={settings.model}
              onChange={(e) => setSettings({ ...settings, model: e.target.value })}
            >
              {!availableModels.includes(settings.model) && settings.model && (
                <option value={settings.model}>{settings.model}</option>
              )}
              {availableModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={settings.model}
              onChange={(e) => setSettings({ ...settings, model: e.target.value })}
              placeholder="Нажмите «Проверить подключение», чтобы загрузить список моделей"
            />
          )}
          {availableModels.length > 0 && (
            <div className="model-hint">Выберите модель из списка, полученного от LM Studio.</div>
          )}
        </label>

        <div className="settings-row">
          <label title="Случайность ответов модели: 0 — точнее, 2 — креативнее. Обычно 0.3–0.7">
            <span className="label-text">Температура</span>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={settings.temperature}
              onChange={(e) => setSettings({ ...settings, temperature: parseFloat(e.target.value) || 0 })}
            />
            {typeof temp === "number" && (temp < 0 || temp > 2) && (
              <span className="field-error">Допустимый диапазон: 0–2</span>
            )}
          </label>
          <label title="Максимальная длина ответа в токенах (256–32768)">
            <span className="label-text">Макс. токенов</span>
            <input
              type="number"
              step="512"
              min="256"
              max="32768"
              value={settings.max_tokens}
              onChange={(e) => setSettings({ ...settings, max_tokens: parseInt(e.target.value) || 4096 })}
            />
            {typeof maxTok === "number" && (maxTok < 256 || maxTok > 32768) && (
              <span className="field-error">Допустимый диапазон: 256–32768</span>
            )}
          </label>
        </div>
        {settings.base_url.trim() && !isValidServerUrl(settings.base_url) && (
          <span className="field-error">Введите корректный URL (http:// или https:// и адрес сервера)</span>
        )}
      </div>

      <div className="settings-actions">
        <button onClick={handleSave} disabled={saving || !settingsValid} title="Сохранить настройки LM Studio на сервере">
          {saving ? "Сохранение…" : saved ? "Сохранено!" : "Сохранить"}
        </button>
        <button className="secondary" onClick={handleTest} disabled={testing} title="Проверить доступность LM Studio и список моделей">
          {testing ? "Проверка…" : "Проверить подключение"}
        </button>
      </div>

      {saveError && (
        <div className="settings-inline-error">
          <span>{saveError}</span>
          <button type="button" className="secondary small" onClick={() => setSaveError(null)}>Закрыть</button>
        </div>
      )}

      {testResult && (
        <div className={"test-result " + (testResult.status === "ok" ? "test-ok" : "test-err")}>
          {testResult.status === "ok" ? (
            <span>
              <strong>Подключено!</strong>
              {testResult.base_url ? ` URL: ${testResult.base_url}. ` : " "}
              {testResult.models && testResult.models.length > 0 ? "Модели загружены." : "Модели не найдены."}
            </span>
          ) : (
            <span><strong>Ошибка подключения:</strong> {testResult.detail}</span>
          )}
        </div>
      )}

      <div className="voices-section">
        <h4>Голосовой ассистент (браузер)</h4>
        <p className="voices-hint">
          Wake-word для фонового режима в модальном voice-окне. По умолчанию: «Гена».
          Работает локально через backend (WebSocket) и доступ к микрофону в браузере.
          STT-модель для распознавания вопроса: `tiny / base / small`.
        </p>
        <div className="settings-row">
          <label title="Ключевое слово, после которого ассистент начнет запись вопроса">
            <span className="label-text">Wake-word</span>
            <input
              type="text"
              value={voiceWakeWord}
              onChange={(e) => setVoiceWakeWord(e.target.value)}
              placeholder="Гена"
              maxLength={48}
            />
          </label>
          <label title="Модель faster-whisper для распознавания вопроса (скорость vs качество)">
            <span className="label-text">STT модель</span>
            <select
              value={voiceSttModel}
              onChange={(e) => setVoiceSttModel(normalizeVoiceSttModel(e.target.value))}
            >
              {VOICE_STT_MODELS.map((m) => (
                <option key={`stt-${m}`} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" onClick={handleSaveVoiceAssistantSettings}>
            {voiceWakeSaved ? "Сохранено!" : "Сохранить voice-настройки"}
          </button>
        </div>
      </div>

      <div className="voices-section">
        <h4>Участники подкаста</h4>
        <p className="voices-hint">
          Единая таблица: роль (для сценария), имя спикера (для скрипта), голос TTS и LLM override.
        </p>
        <p className="voices-hint">
          Роли подставляются автоматически из «Параметры скрипта» / «Формат разговора». Здесь их можно только уточнить вручную, если нужен нестандартный сценарий.
        </p>
        <div className="participants-table-wrap">
          <table className="participants-table">
            <thead>
	              <tr>
	                <th>Роль</th>
	                <th>Имя</th>
	                <th>TTS</th>
	                <th>LLM</th>
	                <th>Действие</th>
	              </tr>
            </thead>
            <tbody>
              {participantDraft.map((row, idx) => {
                const participantName = String(row?.name || "").trim();
                const voiceCfg = (voices.voices && participantName && voices.voices[participantName]) || { model: "", speaker: "0" };
                const llmCfg = (participantName && roleLlmDraft[participantName]) || { model: "", base_url: settings.base_url || "" };
                const voiceTest = participantName ? voiceTestStatus[participantName] : null;
                return (
                  <tr key={`participant-${idx}`}>
	                    <td data-label="Роль">
                      <input
                        type="text"
                        value={row.role || ""}
                        onChange={(e) =>
                          setParticipantDraft((prev) =>
                            prev.map((p, i) => (i === idx ? { ...p, role: e.target.value } : p))
                          )
                        }
                        placeholder={`role_${idx + 1}`}
                      />
                    </td>
	                    <td data-label="Имя">
                      <input
                        type="text"
                        value={row.name || ""}
                        onChange={(e) =>
                          setParticipantDraft((prev) =>
                            prev.map((p, i) => (i === idx ? { ...p, name: e.target.value } : p))
                          )
                        }
                        placeholder={`Спикер ${idx + 1}`}
                      />
                    </td>
	                    <td data-label="TTS">
	                      <div className="participant-cell-stack">
	                        <div className="participant-tts-row">
	                          <select
	                            value={voiceCfg.model || ""}
	                            onChange={(e) =>
	                              setVoices((prev) => {
	                                const nextName = String(participantDraft[idx]?.name || "").trim();
	                                if (!nextName) return prev;
	                                return {
	                                  ...prev,
	                                  voices: {
	                                    ...(prev.voices || {}),
	                                    [nextName]: { model: e.target.value, speaker: "0" },
	                                  },
	                                };
	                              })
	                            }
	                            disabled={!participantName}
	                            title={!participantName ? "Сначала задайте имя участника" : "Выберите голос"}
	                          >
	                            <option value="">— выбрать —</option>
	                            {(voices.available || []).map((opt) => (
	                              <option key={`tts-${idx}-${opt.id}`} value={opt.id}>
	                                {opt.name}
	                              </option>
	                            ))}
	                          </select>
	                          <button
	                            type="button"
	                            className="secondary small icon-only-btn"
	                            onClick={() => participantName && handleTestVoice(participantName)}
	                            disabled={!participantName || !voiceCfg.model || voiceTest === "testing"}
	                            title={
	                              voiceTest === "testing"
	                                ? "Генерация примера…"
	                                : voiceTest === "error"
	                                ? "Ошибка генерации примера"
	                                : "Прослушать короткий пример выбранного голоса"
	                            }
	                            aria-label={
	                              voiceTest === "testing"
	                                ? "Генерация примера"
	                                : voiceTest === "error"
	                                ? "Ошибка генерации примера"
	                                : "Прослушать пример"
	                            }
	                          >
	                            {voiceTest === "testing" ? "…" : voiceTest === "error" ? "!" : "▶"}
	                          </button>
	                        </div>
	                      </div>
	                    </td>
	                    <td data-label="LLM">
                      <div className="participant-cell-stack">
                        {availableModels.length > 0 ? (
                          <select
                            value={llmCfg.model || ""}
                            onChange={(e) =>
                              setRoleLlmDraft((prev) => {
                                if (!participantName) return prev;
                                return {
                                  ...prev,
                                  [participantName]: {
                                    ...((prev && prev[participantName]) || {}),
                                    model: e.target.value,
                                    base_url: llmCfg.base_url || settings.base_url || "",
                                  },
                                };
                              })
                            }
                            disabled={!participantName}
                            title={!participantName ? "Сначала задайте имя участника" : "Модель LLM для участника"}
                          >
                            <option value="">— глобальная модель —</option>
                            {availableModels.map((m) => (
                              <option key={`llm-${idx}-${m}`} value={m}>{m}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="text"
                            value={llmCfg.model || ""}
                            onChange={(e) =>
                              setRoleLlmDraft((prev) => {
                                if (!participantName) return prev;
                                return {
                                  ...prev,
                                  [participantName]: {
                                    ...((prev && prev[participantName]) || {}),
                                    model: e.target.value,
                                    base_url: llmCfg.base_url || settings.base_url || "",
                                  },
                                };
                              })
                            }
                            disabled={!participantName}
                            placeholder="Модель (необязательно)"
                          />
                        )}
                        <input
                          type="text"
                          value={llmCfg.base_url || ""}
                          onChange={(e) =>
                            setRoleLlmDraft((prev) => {
                              if (!participantName) return prev;
                              return {
                                ...prev,
                                [participantName]: {
                                  ...((prev && prev[participantName]) || {}),
                                  model: llmCfg.model || "",
                                  base_url: e.target.value,
                                },
                              };
                            })
                          }
                          disabled={!participantName}
                          placeholder={`Endpoint (по умолчанию: ${settings.base_url || "global"})`}
                          title="Оставьте пустым, чтобы использовать глобальный URL LM Studio"
                        />
                      </div>
                    </td>
	                    <td className="participants-actions-col" data-label="Действие">
                      <button
                        type="button"
                        className="secondary small"
                        onClick={() =>
                          setParticipantDraft((prev) => {
                            const next = prev.filter((_, i) => i !== idx);
                            return next.length ? next : [{ role: "host", name: "Игорь" }];
                          })
                        }
                        disabled={participantDraft.length <= 1}
                      >
                        Удалить
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="settings-actions">
          <button
            type="button"
            className="secondary"
            onClick={() =>
              setParticipantDraft((prev) => [
                ...prev,
                { role: `role_${prev.length + 1}`, name: `Спикер ${prev.length + 1}` },
              ])
            }
          >
            + Добавить участника
          </button>
          <button
            type="button"
            onClick={handleSaveParticipantsAll}
            disabled={savingVoices || savingRoleLlm}
            title="Применить изменения участников и сохранить TTS/LLM по участникам"
          >
            {savingVoices || savingRoleLlm
              ? "Сохранение…"
              : savedVoices && savedRoleLlm
              ? "Участники сохранены!"
              : "Сохранить участников"}
          </button>
        </div>
        {participantsSaveError && (
          <div className="settings-inline-error">
            <span>{participantsSaveError}</span>
            <button
              type="button"
              className="secondary small"
              onClick={() => {
                setSaveVoicesError(null);
                setSaveRoleLlmError(null);
              }}
            >
              Закрыть
            </button>
          </div>
        )}
        <div className="voices-hint">
          Применённые участники: {appliedParticipants.map((p) => `${p.role} → ${p.name}`).join(", ")}
        </div>
      </div>

      <div className="voices-section">
        <h4>Пост-обработка аудио</h4>
        <p className="voices-hint">Loudness, компрессор и лимитер для финального MP3 (офлайн, ffmpeg).</p>
        <div className="settings-grid">
          <label className="checkbox-inline" title="Включает мастеринг после синтеза: нормализацию громкости, компрессию и лимитер по выбранным параметрам.">
            <input type="checkbox" checked={!!post.enabled} onChange={(e) => setPost({ ...post, enabled: e.target.checked })} />
            <span className="checkbox-text">Включить пост-обработку</span>
          </label>
          <label className="checkbox-inline" title="Приводит итоговую громкость к целевому уровню LUFS и выравнивает динамику.">
            <input type="checkbox" checked={!!post.loudnorm} onChange={(e) => setPost({ ...post, loudnorm: e.target.checked })} />
            <span className="checkbox-text">Нормализация громкости (Loudness)</span>
          </label>
          <label className="checkbox-inline" title="Сжимает динамический диапазон: тихие фрагменты становятся слышнее, пики мягче.">
            <input type="checkbox" checked={!!post.compressor} onChange={(e) => setPost({ ...post, compressor: e.target.checked })} />
            <span className="checkbox-text">Компрессор</span>
          </label>
          <label className="checkbox-inline" title="Ограничивает пиковые значения, чтобы избежать перегруза и клиппинга.">
            <input type="checkbox" checked={!!post.limiter} onChange={(e) => setPost({ ...post, limiter: e.target.checked })} />
            <span className="checkbox-text">Лимитер</span>
          </label>
          <label title="Целевая интегральная громкость по стандарту LUFS. Для подкастов обычно -18..-14 LUFS.">
            <span className="label-text">Target LUFS (-30..-5)</span>
            <input type="number" step="0.5" min="-30" max="-5" value={post.target_lufs} onChange={(e) => setPost({ ...post, target_lufs: parseFloat(e.target.value) || -16 })} />
          </label>
          <label title="Максимальный true peak. Обычно ставят около -1.5 dB, чтобы избежать перегрузки на разных устройствах.">
            <span className="label-text">True peak dB (-9..0)</span>
            <input type="number" step="0.1" min="-9" max="0" value={post.true_peak_db} onChange={(e) => setPost({ ...post, true_peak_db: parseFloat(e.target.value) || -1.5 })} />
          </label>
          <label title="LRA — допустимый диапазон динамики. Меньше значение = более ровная, «радио»-подача.">
            <span className="label-text">LRA (1..20)</span>
            <input type="number" step="0.5" min="1" max="20" value={post.lra} onChange={(e) => setPost({ ...post, lra: parseFloat(e.target.value) || 11 })} />
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" onClick={handleSavePost} disabled={savingPost}>
            {savingPost ? "Сохранение…" : savedPost ? "Пост-обработка сохранена!" : "Сохранить пост-обработку"}
          </button>
        </div>
        {savePostError && (
          <div className="settings-inline-error">
            <span>{savePostError}</span>
            <button type="button" className="secondary small" onClick={() => setSavePostError(null)}>Закрыть</button>
          </div>
        )}
      </div>

      <div className="voices-section">
        <h4>OCR ingest</h4>
        <p className="voices-hint">Режим распознавания для PDF/DOCX изображений: быстрый или точный.</p>
        <div className="settings-grid">
          <label className="checkbox-inline" title="Включить OCR для страниц/изображений с недостаточным текстом">
            <input
              type="checkbox"
              checked={!!ocr.enabled}
              onChange={(e) => setOcr({ ...ocr, enabled: e.target.checked })}
            />
            <span className="checkbox-text">Включить OCR</span>
          </label>
          <label title="Fast — быстрее, Accurate — медленнее, но качественнее распознавание">
            <span className="label-text">Режим OCR</span>
            <select value={ocr.mode || "fast"} onChange={(e) => setOcr({ ...ocr, mode: e.target.value })}>
              <option value="fast">Быстрый</option>
              <option value="accurate">Точный</option>
            </select>
          </label>
          <label title="Языки Tesseract, например rus+eng">
            <span className="label-text">Языки OCR</span>
            <input
              type="text"
              value={ocr.lang || "rus+eng"}
              onChange={(e) => setOcr({ ...ocr, lang: e.target.value })}
              placeholder="rus+eng"
            />
          </label>
          <label title="Минимум символов OCR-фрагмента, чтобы добавить его в индекс">
            <span className="label-text">Мин. символов OCR</span>
            <input
              type="number"
              min={1}
              max={120}
              value={ocr.min_chars}
              onChange={(e) => setOcr({ ...ocr, min_chars: parseInt(e.target.value, 10) || 1 })}
            />
          </label>
          <label title="Ограничение числа страниц PDF для OCR">
            <span className="label-text">Макс. страниц PDF</span>
            <input
              type="number"
              min={1}
              max={500}
              value={ocr.max_pdf_pages}
              onChange={(e) => setOcr({ ...ocr, max_pdf_pages: parseInt(e.target.value, 10) || 1 })}
            />
          </label>
          <label title="Ограничение числа изображений DOCX для OCR">
            <span className="label-text">Макс. изображений DOCX</span>
            <input
              type="number"
              min={1}
              max={500}
              value={ocr.max_docx_images}
              onChange={(e) => setOcr({ ...ocr, max_docx_images: parseInt(e.target.value, 10) || 1 })}
            />
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" onClick={handleSaveOcr} disabled={savingOcr}>
            {savingOcr ? "Сохранение…" : savedOcr ? "OCR сохранён!" : "Сохранить OCR"}
          </button>
        </div>
        {saveOcrError && (
          <div className="settings-inline-error">
            <span>{saveOcrError}</span>
            <button type="button" className="secondary small" onClick={() => setSaveOcrError(null)}>Закрыть</button>
          </div>
        )}
      </div>

      <div className="voices-section">
        <h4>Описание изображений через Vision LLM (ingest)</h4>
        <p className="voices-hint">При индексации PDF/DOCX/PPTX изображения отправляются в vision-модель (например Gemma 3) для текстового описания; описание вставляется в текст документа. В Docker укажите Base URL <code>http://host.docker.internal:1234</code>, если LM Studio запущен на хосте.</p>
        <div className="settings-grid">
          <label className="checkbox-inline" title="Включить описание изображений при ingest">
            <input
              type="checkbox"
              checked={!!visionIngest.enabled}
              onChange={(e) => setVisionIngest({ ...visionIngest, enabled: e.target.checked })}
            />
            <span className="checkbox-text">Включить описание изображений через Vision LLM</span>
          </label>
          <label title="Base URL LM Studio (без /v1); для vision используется /api/v1/chat">
            <span className="label-text">Base URL</span>
            <input
              type="text"
              value={visionIngest.base_url || "http://localhost:1234"}
              onChange={(e) => setVisionIngest({ ...visionIngest, base_url: e.target.value })}
              placeholder="http://localhost:1234"
            />
          </label>
          <label title="Идентификатор vision-модели в LM Studio (например google/gemma-3n-e4b)">
            <span className="label-text">Модель (vision)</span>
            <input
              type="text"
              value={visionIngest.model || ""}
              onChange={(e) => setVisionIngest({ ...visionIngest, model: e.target.value })}
              placeholder="модель с поддержкой изображений"
            />
          </label>
          <label title="Таймаут одного запроса к vision-модели (секунды)">
            <span className="label-text">Таймаут (с)</span>
            <input
              type="number"
              min={5}
              max={300}
              value={visionIngest.timeout_seconds ?? 60}
              onChange={(e) => setVisionIngest({ ...visionIngest, timeout_seconds: parseInt(e.target.value, 10) || 60 })}
            />
          </label>
          <label title="Максимум изображений на документ для описания">
            <span className="label-text">Макс. изображений на документ</span>
            <input
              type="number"
              min={1}
              max={100}
              value={visionIngest.max_images_per_document ?? 20}
              onChange={(e) => setVisionIngest({ ...visionIngest, max_images_per_document: parseInt(e.target.value, 10) || 20 })}
            />
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" onClick={handleSaveVisionIngest} disabled={savingVisionIngest}>
            {savingVisionIngest ? "Сохранение…" : savedVisionIngest ? "Vision ingest сохранён!" : "Сохранить Vision ingest"}
          </button>
        </div>
        {saveVisionIngestError && (
          <div className="settings-inline-error">
            <span>{saveVisionIngestError}</span>
            <button type="button" className="secondary small" onClick={() => setSaveVisionIngestError(null)}>Закрыть</button>
          </div>
        )}
      </div>

      <div className="voices-section">
        <h4>Музыка и джинглы (офлайн)</h4>
        <p className="voices-hint">Работает автономно в Docker из локальной папки ассетов.</p>
        <div className="settings-grid">
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={!!music.enabled}
              onChange={(e) => setMusic({ ...music, enabled: e.target.checked })}
            />
            <span className="checkbox-text">Включить музыку в финальном MP3</span>
          </label>
          <label title="Папка с локальными музыкальными файлами (в Docker по умолчанию /opt/audio-assets)">
            <span className="label-text">Папка ассетов</span>
            <input type="text" value={music.assets_dir || ""} onChange={(e) => setMusic({ ...music, assets_dir: e.target.value })} />
          </label>
          <label title="Файл джингла в начале">
            <span className="label-text">Intro файл</span>
            <select
              value={music.intro_file || ""}
              onChange={(e) => setMusic({ ...music, intro_file: e.target.value })}
            >
              <option value="">— выбрать —</option>
              {!musicFiles.includes(music.intro_file || "") && (music.intro_file || "") && (
                <option value={music.intro_file}>{music.intro_file}</option>
              )}
              {musicFiles.map((f) => (
                <option key={`intro-${f}`} value={f}>{f}</option>
              ))}
            </select>
            <div className="music-file-actions">
              <label className="secondary small file-open-btn">
                Открыть файл
                <input
                  type="file"
                  accept=".mp3,.wav,.ogg,.m4a,.aac,.flac,audio/*"
                  hidden
                  onChange={(e) => handleUploadMusic("intro", e.target.files?.[0])}
                />
              </label>
              <button type="button" className="secondary small" onClick={() => playMusicFile(music.intro_file)} disabled={!music.intro_file}>
                Прослушать
              </button>
            </div>
          </label>
          <label title="Файл фона (зацикливается под голос)">
            <span className="label-text">Background файл</span>
            <select
              value={music.background_file || ""}
              onChange={(e) => setMusic({ ...music, background_file: e.target.value })}
            >
              <option value="">— выбрать —</option>
              {!musicFiles.includes(music.background_file || "") && (music.background_file || "") && (
                <option value={music.background_file}>{music.background_file}</option>
              )}
              {musicFiles.map((f) => (
                <option key={`background-${f}`} value={f}>{f}</option>
              ))}
            </select>
            <div className="music-file-actions">
              <label className="secondary small file-open-btn">
                Открыть файл
                <input
                  type="file"
                  accept=".mp3,.wav,.ogg,.m4a,.aac,.flac,audio/*"
                  hidden
                  onChange={(e) => handleUploadMusic("background", e.target.files?.[0])}
                />
              </label>
              <button type="button" className="secondary small" onClick={() => playMusicFile(music.background_file)} disabled={!music.background_file}>
                Прослушать
              </button>
            </div>
          </label>
          <label title="Файл джингла в конце">
            <span className="label-text">Outro файл</span>
            <select
              value={music.outro_file || ""}
              onChange={(e) => setMusic({ ...music, outro_file: e.target.value })}
            >
              <option value="">— выбрать —</option>
              {!musicFiles.includes(music.outro_file || "") && (music.outro_file || "") && (
                <option value={music.outro_file}>{music.outro_file}</option>
              )}
              {musicFiles.map((f) => (
                <option key={`outro-${f}`} value={f}>{f}</option>
              ))}
            </select>
            <div className="music-file-actions">
              <label className="secondary small file-open-btn">
                Открыть файл
                <input
                  type="file"
                  accept=".mp3,.wav,.ogg,.m4a,.aac,.flac,audio/*"
                  hidden
                  onChange={(e) => handleUploadMusic("outro", e.target.files?.[0])}
                />
              </label>
              <button type="button" className="secondary small" onClick={() => playMusicFile(music.outro_file)} disabled={!music.outro_file}>
                Прослушать
              </button>
            </div>
          </label>
          <label>
            <span className="label-text">Громкость intro (0..2)</span>
            <input type="number" step="0.05" min="0" max="2" value={music.intro_volume} onChange={(e) => setMusic({ ...music, intro_volume: parseFloat(e.target.value) || 0 })} />
          </label>
          <label>
            <span className="label-text">Громкость фона (0..2)</span>
            <input type="number" step="0.05" min="0" max="2" value={music.background_volume} onChange={(e) => setMusic({ ...music, background_volume: parseFloat(e.target.value) || 0 })} />
          </label>
          <label>
            <span className="label-text">Громкость outro (0..2)</span>
            <input type="number" step="0.05" min="0" max="2" value={music.outro_volume} onChange={(e) => setMusic({ ...music, outro_volume: parseFloat(e.target.value) || 0 })} />
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" onClick={handleSaveMusic} disabled={savingMusic}>
            {savingMusic ? "Сохранение…" : savedMusic ? "Музыка сохранена!" : "Сохранить музыку"}
          </button>
          <button type="button" className="secondary" onClick={stopMusicPreview} disabled={!musicPreviewRef.current}>
            Остановить прослушивание
          </button>
          {uploadingMusicSlot && <span className="text-muted">Загрузка файла для {uploadingMusicSlot}…</span>}
        </div>
        {musicPreviewName && <div className="model-hint">Сейчас играет: {musicPreviewName}</div>}
        {saveMusicError && (
          <div className="settings-inline-error">
            <span>{saveMusicError}</span>
            <button type="button" className="secondary small" onClick={() => setSaveMusicError(null)}>Закрыть</button>
          </div>
        )}
      </div>

      <div className="voices-section">
        <h4>Словарь произношений (TTS)</h4>
        <p className="voices-hint">Личные замены для сложных слов и брендов. Пример: telegram → телеграмм.</p>
        {Object.keys(pronHiddenOverrides).length > 0 && (
          <p className="voices-hint">Показаны последние 3 правила. Скрытых правил: {Object.keys(pronHiddenOverrides).length}.</p>
        )}
        <div className="pron-grid">
          {pronRows.map((row, i) => (
            <div key={i} className="pron-row">
              <input
                type="text"
                placeholder="что заменить"
                value={row.from}
                onChange={(e) => setPronRow(i, { from: e.target.value })}
              />
              <input
                type="text"
                placeholder="как произносить"
                value={row.to}
                onChange={(e) => setPronRow(i, { to: e.target.value })}
              />
              <button
                type="button"
                className="secondary small"
                onClick={() => removePronRow(i)}
                disabled={pronRows.length <= 1}
              >
                Удалить
              </button>
            </div>
          ))}
        </div>
        <div className="settings-actions">
          <button type="button" className="secondary" onClick={handleExportPron}>
            Экспорт JSON
          </button>
          <label className="secondary file-open-btn">
            Импорт JSON
            <input
              type="file"
              accept=".json,application/json"
              hidden
              onChange={(e) => handleImportPron(e.target.files?.[0])}
            />
          </label>
          <button type="button" className="secondary" onClick={addPronRow}>Добавить правило</button>
          <button type="button" className="secondary" onClick={handleResetPron} disabled={savingPron}>
            Сброс словаря
          </button>
          <button type="button" onClick={handleSavePron} disabled={savingPron}>
            {savingPron ? "Сохранение…" : savedPron ? "Словарь сохранён!" : "Сохранить словарь"}
          </button>
        </div>
        {savePronError && (
          <div className="settings-inline-error">
            <span>{savePronError}</span>
            <button type="button" className="secondary small" onClick={() => setSavePronError(null)}>Закрыть</button>
          </div>
        )}
      </div>

      <div className="voices-section danger-zone">
        <h4>Очистка базы</h4>
        <p className="voices-hint">
          Удаляет документы, индексы Chroma, чат-историю, задания и все сгенерированные файлы. Действие необратимо.
        </p>
        <div className="settings-actions">
          <button type="button" className="danger-btn" onClick={handleResetDatabase} disabled={dbResetting}>
            {dbResetting ? "Очистка…" : savedDbReset ? "База очищена" : "Очистить базу полностью"}
          </button>
        </div>
        {dbResetError && (
          <div className="settings-inline-error">
            <span>{dbResetError}</span>
            <button type="button" className="secondary small" onClick={() => setDbResetError(null)}>Закрыть</button>
          </div>
        )}
      </div>
    </div>
  );
}
