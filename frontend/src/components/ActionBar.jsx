import React, { useEffect, useRef, useState } from "react";
import {
  ingestJob,
  consumeSummaryStream,
  consumeScriptStream,
  generateScriptOutline,
  generateAudio,
  getStyleProfiles,
  getScriptScenarios,
  upsertScriptScenario,
  deleteScriptScenario,
  upsertStyleProfile,
  deleteStyleProfile,
  getQualityReport,
} from "../api/client";
import { useJobPoller } from "../hooks/useJobPoller";
import "./ActionBar.css";

const OUTLINE_AUTO_APPROVE_SECONDS = 30;

const FALLBACK_SCENARIO_PROFILES = [
  { id: "classic_overview", name: "Классический обзор", hint: "Ведущий и гости обсуждают документ." },
  { id: "interview", name: "Интервью", hint: "Ведущий задаёт вопросы, гости отвечают по документу." },
  { id: "debate", name: "Дебаты / спор", hint: "Аргументы и контраргументы по материалу." },
  { id: "critique", name: "Критика и улучшения", hint: "Краткий пересказ документа, затем критика, риски и улучшения." },
  { id: "round_table", name: "Круглый стол", hint: "Несколько ролей с разными точками зрения." },
  { id: "educational", name: "Образовательный", hint: "Учитель объясняет, ученик задаёт вопросы." },
  { id: "news_digest", name: "Новостной дайджест", hint: "Короткие блоки 'главное' с настраиваемым тоном." },
  { id: "investigation", name: "Расследование", hint: "Гипотезы, проверка по тексту, выводы." },
];

function normalizeScenarioProfiles(list) {
  return (Array.isArray(list) ? list : [])
    .filter((x) => x && x.id)
    .map((x) => ({
      id: x.id,
      name: x.name || x.id,
      hint: x.description || x.hint || "",
      is_builtin: !!x.is_builtin,
      min_roles: x.min_roles,
      max_roles: x.max_roles,
      default_roles: Array.isArray(x.default_roles) ? x.default_roles : undefined,
      supported_options: Array.isArray(x.supported_options) ? x.supported_options : undefined,
    }));
}

function clampMinutes(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 5;
  return Math.min(60, Math.max(1, Math.round(n)));
}

function generationModeLabel(mode) {
  return mode === "turn_taking" ? "Пошагово по ролям" : "Один проход";
}

function normalizeKnowledgeMode(raw) {
  const v = String(raw || "document_only").trim().toLowerCase();
  return v === "hybrid_model" ? "hybrid_model" : "document_only";
}

function scenarioSupportsHybrid(rawScenario) {
  const key = String(rawScenario || "").trim().toLowerCase();
  return key === "debate" || key === "critique";
}

export default function ActionBar({
  documentId,
  ingested,
  chunks,
  hasScript,
  participants = [],
  roleLlmMap = {},
  onScenarioRolesChange,
  onIngested,
  onSummary,
  onStreamingSummary,
  onScript,
  onStreamingScript,
  onAudioJob,
  onScriptSettingsChange,
  projectDefaults = null,
  onError,
}) {
  const [busy, setBusy] = useState("");
  const [minutes, setMinutes] = useState(5);
  const [minutesInput, setMinutesInput] = useState("5");
  const [style, setStyle] = useState("conversational");
  const [scenario, setScenario] = useState("classic_overview");
  const [generationMode, setGenerationMode] = useState("single_pass");
  const [focus, setFocus] = useState("");
  const [scenarioProfiles, setScenarioProfiles] = useState(FALLBACK_SCENARIO_PROFILES);
  const [scenarioDraft, setScenarioDraft] = useState({
    debate_stance_a: "скептик",
    debate_stance_b: "оптимист",
    news_block_count: 4,
    news_tone: "нейтральный",
  });
  const [customScenarioName, setCustomScenarioName] = useState("");
  const [customScenarioInstruction, setCustomScenarioInstruction] = useState("");
  const [customScenarioMinRoles, setCustomScenarioMinRoles] = useState(1);
  const [customScenarioMaxRoles, setCustomScenarioMaxRoles] = useState(8);
  const [styleProfiles, setStyleProfiles] = useState([
    { id: "conversational", name: "Разговорный", instruction: "Тон дружелюбный и естественный, короткие реплики." },
    { id: "educational", name: "Образовательный", instruction: "Тезис, объяснение, пример и вывод." },
    { id: "debate", name: "Дебаты", instruction: "Аргументы и контраргументы между спикерами." },
    { id: "interview", name: "Интервью", instruction: "Ведущий задаёт вопросы, гости отвечают развёрнуто." },
  ]);
  const [ttsFriendly, setTtsFriendly] = useState(true);
  const [knowledgeMode, setKnowledgeMode] = useState("document_only");
  const [customStyleName, setCustomStyleName] = useState("");
  const [customStyleInstruction, setCustomStyleInstruction] = useState("");
  const [qualityReport, setQualityReport] = useState(null);
  const [qualityBusy, setQualityBusy] = useState(false);
  const [summaryChars, setSummaryChars] = useState(0);
  const [scriptChars, setScriptChars] = useState(0);
  const [scriptStatusMessage, setScriptStatusMessage] = useState("");
  const [promptDebug, setPromptDebug] = useState(null);
  const [outlineApproval, setOutlineApproval] = useState(null);
  const outlineAutoTriggeredRef = useRef(false);
  const projectDefaultsKeyRef = useRef("");
  const { job: ingestJobState, startPolling: startIngestPolling, reset: resetIngestPolling } = useJobPoller();
  const { job: audioJob, startPolling: startAudioPolling, reset: resetAudioPolling } = useJobPoller();

  useEffect(() => {
    let cancelled = false;
    getStyleProfiles()
      .then((res) => {
        if (cancelled) return;
        const list = (res && res.profiles) || [];
        if (Array.isArray(list) && list.length > 0) {
          setStyleProfiles(list.map((x) => ({ id: x.id, name: x.name || x.id, instruction: x.instruction || "" })));
          setStyle((prev) => (list.find((x) => x.id === prev) ? prev : list[0].id));
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getScriptScenarios()
      .then((res) => {
        if (cancelled) return;
        const list = (res && res.scenarios) || [];
        if (!Array.isArray(list) || list.length === 0) return;
        const normalized = normalizeScenarioProfiles(list);
        if (normalized.length === 0) return;
        setScenarioProfiles(normalized);
        setScenario((prev) => (normalized.find((x) => x.id === prev) ? prev : normalized[0].id));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const scriptCfg = projectDefaults && typeof projectDefaults === "object" ? projectDefaults.script : null;
    if (!scriptCfg || typeof scriptCfg !== "object") {
      projectDefaultsKeyRef.current = "";
      return;
    }
    const key = JSON.stringify(scriptCfg);
    if (projectDefaultsKeyRef.current === key) return;
    projectDefaultsKeyRef.current = key;
    const nextMinutes = clampMinutes(scriptCfg.minutes);
    const nextStyle = String(scriptCfg.style || "").trim();
    const nextScenario = String(scriptCfg.scenario || "").trim();
    const nextFocus = String(scriptCfg.focus || "");
    const nextGenerationMode = String(scriptCfg.generation_mode || "").trim().toLowerCase();
    const nextTtsFriendly = scriptCfg.tts_friendly == null ? true : !!scriptCfg.tts_friendly;
    const nextKnowledgeMode = normalizeKnowledgeMode(scriptCfg.knowledge_mode);
    setMinutes(nextMinutes);
    setMinutesInput(String(nextMinutes));
    if (nextStyle) setStyle(nextStyle);
    if (nextScenario) setScenario(nextScenario);
    setFocus(nextFocus);
    setGenerationMode(nextGenerationMode === "turn_taking" ? "turn_taking" : "single_pass");
    setTtsFriendly(nextTtsFriendly);
    setKnowledgeMode(nextKnowledgeMode);
    if (scriptCfg.scenario_options && typeof scriptCfg.scenario_options === "object") {
      setScenarioDraft((prev) => ({ ...prev, ...scriptCfg.scenario_options }));
    }
  }, [projectDefaults]);

  async function wrap(key, fn) {
    setBusy(key);
    try {
      await fn();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy("");
    }
  }

  const currentStyleProfile = styleProfiles.find((p) => p.id === style);
  const currentStyleHint = currentStyleProfile?.instruction || "";
  const currentScenarioProfile = scenarioProfiles.find((p) => p.id === scenario);
  const currentScenarioHint = currentScenarioProfile?.hint || "";
  const participantRows = (Array.isArray(participants) ? participants : []).filter(Boolean);
  const participantNames = participantRows.map((p) => String(p?.name || "").trim()).filter(Boolean);
  const participantRoles = participantRows.map((p) => String(p?.role || "").trim()).filter(Boolean);
  const recommendedScenarioRoles = Array.isArray(currentScenarioProfile?.default_roles)
    ? currentScenarioProfile.default_roles.filter(Boolean)
    : [];
  const ingestProgress = Math.max(0, Math.min(100, Number(ingestJobState?.progress || 0)));
  const ingestActive = ["pending", "running"].includes(String(ingestJobState?.status || ""));
  const audioProgress = Math.max(0, Math.min(100, Number(audioJob?.progress || 0)));
  const audioActive = ["pending", "running"].includes(String(audioJob?.status || ""));

  useEffect(() => {
    resetIngestPolling();
    resetAudioPolling();
    setSummaryChars(0);
    setScriptChars(0);
    setPromptDebug(null);
    setOutlineApproval(null);
    outlineAutoTriggeredRef.current = false;
  }, [documentId, resetAudioPolling, resetIngestPolling]);

  useEffect(() => {
    if (!outlineApproval?.open || !outlineApproval.autoEnabled || outlineApproval.touched) return;
    if (busy === "script") return;
    if (outlineApproval.countdown <= 0) {
      if (outlineAutoTriggeredRef.current) return;
      outlineAutoTriggeredRef.current = true;
      approveOutlineAndGenerate(true).catch((e) => onError(e?.message || "Не удалось запустить генерацию по плану"));
      return;
    }
    const t = setTimeout(() => {
      setOutlineApproval((prev) => {
        if (!prev || !prev.open || !prev.autoEnabled || prev.touched) return prev;
        return { ...prev, countdown: Math.max(0, Number(prev.countdown || 0) - 1) };
      });
    }, 1000);
    return () => clearTimeout(t);
  }, [outlineApproval, busy]);

  useEffect(() => {
    if (ingestJobState?.status !== "done") return;
    const first = (ingestJobState.output_paths || [])[0] || "";
    const m = String(first).match(/^chunks:(\d+)$/);
    const n = m ? parseInt(m[1], 10) : chunks;
    onIngested(Number.isFinite(n) ? n : chunks);
  }, [ingestJobState?.status, ingestJobState?.output_paths, chunks, onIngested]);

  useEffect(() => {
    if (!currentScenarioProfile || currentScenarioProfile.is_builtin) return;
    setCustomScenarioName(currentScenarioProfile.name || "");
    setCustomScenarioInstruction(currentScenarioProfile.hint || "");
    setCustomScenarioMinRoles(Math.max(1, Number(currentScenarioProfile.min_roles) || 1));
    setCustomScenarioMaxRoles(Math.max(1, Number(currentScenarioProfile.max_roles) || 8));
  }, [currentScenarioProfile?.id]);

  useEffect(() => {
    const nextRoles = Array.isArray(currentScenarioProfile?.default_roles)
      ? currentScenarioProfile.default_roles.map((x) => String(x || "").trim()).filter(Boolean)
      : [];
    if (!nextRoles.length) return;
    onScenarioRolesChange?.(nextRoles);
  }, [currentScenarioProfile?.id, JSON.stringify(currentScenarioProfile?.default_roles || []), onScenarioRolesChange]);

  function buildScenarioOptions() {
    if (scenario === "debate") {
      return {
        stance_a: String(scenarioDraft.debate_stance_a || "").trim() || "скептик",
        stance_b: String(scenarioDraft.debate_stance_b || "").trim() || "оптимист",
      };
    }
    if (scenario === "news_digest") {
      return {
        block_count: Math.max(2, Math.min(12, Number(scenarioDraft.news_block_count) || 4)),
        tone: String(scenarioDraft.news_tone || "").trim() || "нейтральный",
      };
    }
    return {};
  }

  function buildScriptBody(extra = {}) {
    const voiceList = participantNames.length ? participantNames : ["Игорь", "Аня", "Максим"];
    return {
      minutes: Math.min(60, Math.max(1, Number(minutes) || 5)),
      style,
      focus: String(focus || "").trim() || undefined,
      voices: voiceList,
      scenario,
      scenario_options: buildScenarioOptions(),
      generation_mode: generationMode,
      role_llm_map: roleLlmMap && typeof roleLlmMap === "object" ? roleLlmMap : undefined,
      tts_friendly: ttsFriendly,
      knowledge_mode: scenarioSupportsHybrid(scenario) ? normalizeKnowledgeMode(knowledgeMode) : "document_only",
      ...extra,
    };
  }

  async function runScriptStream(body) {
    setScriptChars(0);
    setScriptStatusMessage("");
    setPromptDebug(null);
    await consumeScriptStream(documentId, body, {
      onChunk: (partial) => {
        setScriptChars((partial || "").length);
        onStreamingScript?.(partial);
      },
      onStatus: (evt) => {
        if (evt?.warning) onError(evt.warning);
        if (evt?.prompt_debug && typeof evt.prompt_debug === "object") {
          setPromptDebug(evt.prompt_debug);
        }
        if (evt?.message) setScriptStatusMessage(evt.message);
      },
      onDone: (scriptArr) => onScript(scriptArr),
      onError: (e) => {
        setScriptStatusMessage("");
        onError(e?.message);
      },
    });
    setScriptStatusMessage("");
  }

  async function requestOutlineApproval() {
    const body = buildScriptBody();
    setBusy("script_outline");
    try {
      const res = await generateScriptOutline(documentId, body);
      const outline = (res && res.outline) || {};
      if (res && res.prompt_debug && typeof res.prompt_debug === "object") {
        setPromptDebug(res.prompt_debug);
      } else {
        setPromptDebug(null);
      }
      const text = JSON.stringify(outline, null, 2);
      outlineAutoTriggeredRef.current = false;
      setOutlineApproval({
        open: true,
        text,
        initialText: text,
        countdown: OUTLINE_AUTO_APPROVE_SECONDS,
        autoEnabled: true,
        touched: false,
        body,
      });
    } finally {
      setBusy("");
    }
  }

  async function approveOutlineAndGenerate(isAuto = false) {
    if (!outlineApproval?.open) return;
    let outlinePlan;
    try {
      outlinePlan = JSON.parse(outlineApproval.text);
    } catch {
      onError("План должен быть валидным JSON. Исправьте формат или отмените авто-запуск.");
      return;
    }
    const finalBody = { ...(outlineApproval.body || buildScriptBody()), outline_plan: outlinePlan };
    setOutlineApproval((prev) => (prev ? { ...prev, autoEnabled: false, countdown: 0 } : prev));
    if (!isAuto) outlineAutoTriggeredRef.current = true;
    await wrap("script", async () => {
      await runScriptStream(finalBody);
      setOutlineApproval(null);
    });
  }

  const scriptBusy = busy === "script" || busy === "script_outline";

  const roleSetLower = new Set(participantRoles.map((x) => x.toLowerCase()));
  const missingScenarioRoles = recommendedScenarioRoles.filter((r) => !roleSetLower.has(String(r).toLowerCase()));
  const extraScenarioRoles = participantRoles.filter(
    (r) => recommendedScenarioRoles.length > 0 && !recommendedScenarioRoles.some((x) => String(x).toLowerCase() === String(r).toLowerCase())
  );
  const scenarioMinRoles = Math.max(1, Number(currentScenarioProfile?.min_roles) || 1);
  const scenarioMaxRoles = Math.max(scenarioMinRoles, Number(currentScenarioProfile?.max_roles) || 99);
  let roleMappingLevel = "ok";
  let roleMappingSummary = "Роли сценария и участники согласованы.";
  if (participantRoles.length < scenarioMinRoles) {
    roleMappingLevel = "error";
    roleMappingSummary = `Недостаточно ролей: нужно минимум ${scenarioMinRoles}, сейчас ${participantRoles.length}.`;
  } else if (participantRoles.length > scenarioMaxRoles) {
    roleMappingLevel = "warn";
    roleMappingSummary = `Ролей больше допустимого для сценария: максимум ${scenarioMaxRoles}, сейчас ${participantRoles.length}.`;
  } else if (missingScenarioRoles.length > 0) {
    roleMappingLevel = "error";
    roleMappingSummary = `Не хватает ролей сценария: ${missingScenarioRoles.join(", ")}.`;
  } else if (extraScenarioRoles.length > 0 && recommendedScenarioRoles.length > 0) {
    roleMappingLevel = "warn";
    roleMappingSummary = `Есть дополнительные роли: ${extraScenarioRoles.join(", ")}.`;
  } else if (recommendedScenarioRoles.length === 0) {
    roleMappingLevel = "ok";
    roleMappingSummary = "Сценарий без фиксированного набора ролей.";
  }
  const promptDebugQuery = String(promptDebug?.retrieval_query || "").trim();
  const settingsDigest = [
    { label: "Длительность", value: `${Math.min(60, Math.max(1, Number(minutes) || 5))} мин` },
    { label: "Тон речи", value: currentStyleProfile?.name || style },
    { label: "Формат разговора", value: currentScenarioProfile?.name || scenario },
    { label: "Режим генерации", value: generationModeLabel(generationMode) },
    { label: "Источник идей", value: scenarioSupportsHybrid(scenario) ? (knowledgeMode === "hybrid_model" ? "документ + знания модели" : "только документ") : "только документ" },
    { label: "TTS-оптимизация", value: ttsFriendly ? "включена" : "выключена" },
  ];
  const presetMode =
    generationMode === "turn_taking" && Number(minutes) >= 10
      ? "deep"
      : Number(minutes) <= 3
      ? "quick"
      : "balanced";

  function setScenarioIfExists(nextScenario) {
    const candidate = String(nextScenario || "").trim();
    if (!candidate) return;
    if (scenarioProfiles.some((row) => row.id === candidate)) {
      setScenario(candidate);
    }
  }

  function applyScriptPreset(kind) {
    if (kind === "quick") {
      setMinutes(3);
      setMinutesInput("3");
      setGenerationMode("single_pass");
      setStyle((prev) => (styleProfiles.some((p) => p.id === "conversational") ? "conversational" : prev));
      setScenarioIfExists("classic_overview");
      setTtsFriendly(true);
      setKnowledgeMode("document_only");
      return;
    }
    if (kind === "deep") {
      setMinutes(12);
      setMinutesInput("12");
      setGenerationMode("turn_taking");
      setStyle((prev) => (styleProfiles.some((p) => p.id === "educational") ? "educational" : prev));
      setScenarioIfExists("critique");
      setTtsFriendly(true);
      setKnowledgeMode("hybrid_model");
      return;
    }
    setMinutes(5);
    setMinutesInput("5");
    setGenerationMode("single_pass");
    setStyle((prev) => (styleProfiles.some((p) => p.id === "conversational") ? "conversational" : prev));
    setScenarioIfExists("classic_overview");
    setTtsFriendly(true);
  }

  useEffect(() => {
    onScriptSettingsChange?.({
      minutes: Math.min(60, Math.max(1, Number(minutes) || 5)),
      style,
      focus: String(focus || "").trim(),
      scenario,
      scenario_options: buildScenarioOptions(),
      generation_mode: generationMode,
      role_llm_map: roleLlmMap && typeof roleLlmMap === "object" ? roleLlmMap : {},
      tts_friendly: !!ttsFriendly,
      knowledge_mode: normalizeKnowledgeMode(knowledgeMode),
    });
  }, [minutes, style, focus, scenario, scenarioDraft, generationMode, ttsFriendly, knowledgeMode, roleLlmMap, onScriptSettingsChange]);

  useEffect(() => {
    if (!scenarioSupportsHybrid(scenario) && knowledgeMode !== "document_only") {
      setKnowledgeMode("document_only");
    }
  }, [scenario, knowledgeMode]);

  useEffect(() => {
    setPromptDebug(null);
  }, [minutes, style, focus, scenario, scenarioDraft, generationMode, ttsFriendly, knowledgeMode, documentId]);

  return (
    <div className="card action-bar">
      {/* Step 1: Ingest */}
      <div className="action-row">
        <button
          className="step-btn"
          disabled={!documentId || busy === "ingest" || ingestActive}
          title={
            ingested
              ? "Переиндексировать документ (пересобрать индекс в базе)"
              : "Разбить документ на фрагменты для поиска и саммари"
          }
          onClick={() =>
            wrap("ingest", async () => {
              const res = await ingestJob(documentId);
              startIngestPolling(res.job_id);
            })
          }
        >
          <span className="step-btn-label">
            {busy === "ingest"
              ? "Запуск индексации…"
              : ingestActive
              ? `Индексация (${ingestProgress}%)`
              : ingested
              ? `Переиндексировать (${chunks} фрагментов)`
              : "1. Индексация"}
          </span>
          {(busy === "ingest" || ingestJobState) && (
            <span
              className={busy === "ingest" ? "step-progress is-indeterminate" : "step-progress"}
              style={busy === "ingest" ? undefined : { width: `${ingestProgress}%` }}
            />
          )}
        </button>

        {/* Step 2: Summary (streaming) */}
        <button
          className="step-btn"
          disabled={!ingested || busy === "summary"}
          title={!ingested ? "Сначала выполните индексацию" : "Сгенерировать краткое изложение документа"}
          onClick={() =>
            wrap("summary", async () => {
              setSummaryChars(0);
              await consumeSummaryStream(documentId, {
                onChunk: (partial) => {
                  setSummaryChars((partial || "").length);
                  onStreamingSummary?.(partial);
                },
                onDone: (s, src) => onSummary(s, src),
                onError: (e) => onError(e?.message),
              });
            })
          }
        >
          <span className="step-btn-label">
            {busy === "summary" ? `Генерация… ${summaryChars} симв.` : "2. Саммари"}
          </span>
          {busy === "summary" && <span className="step-progress is-indeterminate" />}
        </button>

        {/* Step 3: Script (streaming) */}
        <button
          className="step-btn"
          disabled={!ingested || scriptBusy}
          title={!ingested ? "Сначала выполните индексацию" : "Создать скрипт подкаста по заданным параметрам"}
          onClick={() =>
            (generationMode === "turn_taking"
              ? wrap("script_outline", async () => {
                  await requestOutlineApproval();
                })
              : wrap("script", async () => {
                  await runScriptStream(buildScriptBody());
                }))
          }
        >
          <span className="step-btn-label">
            {busy === "script"
              ? scriptStatusMessage
                ? `${scriptStatusMessage} ${scriptChars ? `(${scriptChars} симв.)` : ""}`.trim()
                : `Пишу скрипт… ${scriptChars} симв.`
              : busy === "script_outline"
              ? "Готовлю план…"
              : "3. Сгенерировать скрипт"}
          </span>
          {scriptBusy && <span className="step-progress is-indeterminate" />}
        </button>

        {/* Step 4: Audio */}
        <button
          className="accent step-btn"
          disabled={!hasScript || busy === "audio" || audioActive}
          title={!hasScript ? "Сначала создайте скрипт подкаста" : "Запустить фоновую генерацию аудио подкаста"}
          onClick={() =>
            wrap("audio", async () => {
              const res = await generateAudio(documentId);
              onAudioJob(res.job_id);
              startAudioPolling(res.job_id);
            })
          }
        >
          <span className="step-btn-label">
            {busy === "audio" ? "Запуск…" : audioActive ? `4. Генерация аудио (${audioProgress}%)` : "4. Сгенерировать аудио"}
          </span>
          {(busy === "audio" || audioJob) && (
            <span
              className={busy === "audio" ? "step-progress is-indeterminate" : "step-progress"}
              style={busy === "audio" ? undefined : { width: `${audioProgress}%` }}
            />
          )}
        </button>

      </div>

      {outlineApproval?.open && (
        <div className="action-note outline-approval-card">
          <div className="outline-approval-head">
            <div>
              <strong>Предварительный план выпуска</strong>
              <div className="style-hint">
                Проверьте и при необходимости поправьте JSON-план. Если не начнёте редактировать, генерация стартует автоматически.
              </div>
            </div>
            <div className={`outline-auto-badge ${outlineApproval.autoEnabled && !outlineApproval.touched ? "is-active" : ""}`}>
              {outlineApproval.autoEnabled && !outlineApproval.touched
                ? `Автозапуск через ${outlineApproval.countdown} c`
                : "Автозапуск остановлен"}
            </div>
          </div>
          <textarea
            className="outline-editor"
            value={outlineApproval.text}
            rows={14}
            spellCheck={false}
            onChange={(e) => {
              const nextText = e.target.value;
              outlineAutoTriggeredRef.current = false;
              setOutlineApproval((prev) =>
                prev
                  ? {
                      ...prev,
                      text: nextText,
                      touched: true,
                      autoEnabled: false,
                    }
                  : prev
              );
            }}
          />
          <div className="action-inline-group">
            <button type="button" onClick={() => approveOutlineAndGenerate(false)} disabled={scriptBusy}>
              Запустить по плану
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                outlineAutoTriggeredRef.current = false;
                setOutlineApproval(null);
              }}
              disabled={scriptBusy}
            >
              Отмена
            </button>
            {outlineApproval.touched && (
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  setOutlineApproval((prev) =>
                    prev
                      ? {
                          ...prev,
                          text: prev.initialText,
                          touched: false,
                          autoEnabled: true,
                          countdown: OUTLINE_AUTO_APPROVE_SECONDS,
                        }
                      : prev
                  )
                }
                disabled={scriptBusy}
                title="Вернуть исходный план и снова включить автозапуск"
              >
                Сбросить правки
              </button>
            )}
          </div>
        </div>
      )}

      {/* Script settings */}
      <details className="script-settings">
        <summary title="Длительность, стиль и роли голосов для скрипта подкаста">Параметры скрипта</summary>
        <div className="settings-digest" aria-label="Текущий профиль генерации">
          {settingsDigest.map((row) => (
            <span key={row.label} className="settings-digest-chip">
              <b>{row.label}:</b> {row.value}
            </span>
          ))}
        </div>
        <div className="settings-presets" role="group" aria-label="Быстрые пресеты генерации">
          <button
            type="button"
            className={`secondary small ${presetMode === "quick" ? "is-active" : ""}`.trim()}
            onClick={() => applyScriptPreset("quick")}
            title="Короткий выпуск: быстрая однопроходная генерация"
          >
            Быстро
          </button>
          <button
            type="button"
            className={`secondary small ${presetMode === "balanced" ? "is-active" : ""}`.trim()}
            onClick={() => applyScriptPreset("balanced")}
            title="Базовый сбалансированный пресет"
          >
            Баланс
          </button>
          <button
            type="button"
            className={`secondary small ${presetMode === "deep" ? "is-active" : ""}`.trim()}
            onClick={() => applyScriptPreset("deep")}
            title="Более длинный выпуск с пошаговым режимом"
          >
            Глубоко
          </button>
        </div>
        <div className="settings-grid">
          <div className="settings-section-title">Основное</div>
          <label title="Целевая длительность подкаста в минутах (1–60)">
            Длительность (мин)
            <input
              type="number"
              min="1"
              max="60"
              value={minutesInput}
              onChange={(e) => {
                const v = e.target.value;
                setMinutesInput(v);
                const n = parseInt(v, 10);
                if (!Number.isNaN(n)) {
                  const clamped = Math.min(60, Math.max(1, n));
                  setMinutes(clamped);
                  if (clamped !== n) setMinutesInput(String(clamped));
                }
              }}
              onBlur={() => {
                const n = parseInt(minutesInput, 10);
                if (Number.isNaN(n) || n < 1 || n > 60) {
                  setMinutesInput(String(minutes));
                }
              }}
            />
            {(() => {
              const n = parseInt(minutesInput, 10);
              if (minutesInput !== "" && (Number.isNaN(n) || n < 1 || n > 60)) {
                return <span className="field-error">Допустимый диапазон: 1–60 мин</span>;
              }
              return null;
            })()}
          </label>
          <label title="Профиль подачи. Определяет тон и манеру речи, а не структуру ролей.">
            Тон речи
            <select
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              title={currentStyleHint || "Выберите стиль скрипта"}
            >
              {styleProfiles.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            {currentStyleHint && <span className="style-hint">{currentStyleHint}</span>}
            <span className="style-hint">Структуру разговора задаёт поле «Формат разговора».</span>
          </label>
          <label title="Сценарий задаёт структуру диалога и распределение ролей в подкасте">
            Формат разговора
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              title={currentScenarioHint || "Выберите сценарий"}
            >
              {scenarioProfiles.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            {currentScenarioHint && <span className="style-hint">{currentScenarioHint}</span>}
            {currentScenarioProfile?.default_roles?.length > 0 && (
              <span className="style-hint">
                Рекомендуемые роли: {currentScenarioProfile.default_roles.join(", ")}
              </span>
            )}
            <span className="style-hint">
              {currentScenarioProfile?.is_builtin ? "Встроенный сценарий" : "Пользовательский сценарий"}
            </span>
          </label>
          <label title="Определяет, может ли модель добавлять идеи и критику сверх документа">
            Источник идей
            <select
              value={scenarioSupportsHybrid(scenario) ? knowledgeMode : "document_only"}
              onChange={(e) => setKnowledgeMode(normalizeKnowledgeMode(e.target.value))}
              disabled={!scenarioSupportsHybrid(scenario)}
            >
              <option value="document_only">Только документ</option>
              <option value="hybrid_model">Документ + знания модели</option>
            </select>
            <span className="style-hint">
              {scenarioSupportsHybrid(scenario)
                ? "Для дебатов и критики можно подключать внешние знания модели. Внешние идеи будут маркироваться как вне документа."
                : "Для выбранного сценария используется только документ."}
            </span>
          </label>
          <label
            className="settings-span-2"
            title="Необязательно. Помогает RAG точнее подобрать фрагменты документа для выпуска."
          >
            Фокус выпуска (о чём выпуск)
            <input
              type="text"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="Например: как использовать Taiga в образовательных проектах"
            />
            <span className="style-hint">
              Используется в поисковом запросе к фрагментам вместе с названием документа, форматом и тоном речи.
            </span>
          </label>
          <div className={`action-note role-mapping-status ${roleMappingLevel === "error" ? "is-error" : roleMappingLevel === "warn" ? "is-warn" : "is-ok"}`}>
            <strong>Маппинг ролей сценария</strong>
            <span>{roleMappingSummary}</span>
            <span className="style-hint">
              Участники: {participantRows.length}. Роли задаются в Настройки → Участники.
            </span>
            {recommendedScenarioRoles.length > 0 && (
              <span className="style-hint">Ожидаемые роли: {recommendedScenarioRoles.join(", ")}</span>
            )}
          </div>
          {promptDebug && (
            <div className="action-note prompt-debug-card">
              <strong>Диагностика контекста prompt</strong>
              <div className="prompt-debug-grid">
                <span>Режим генерации: <b>{promptDebug.mode === "turn_taking" ? "Пошагово по ролям" : "Один проход"}</b></span>
                <span>
                  Chunks: <b>{Number(promptDebug.chunks_selected || 0)}</b>
                  {" / "}
                  лимит <b>{Number(promptDebug.chunk_limit || 0)}</b>
                </span>
                <span>
                  Контекст документа: <b>{Number(promptDebug.doc_context_chars || 0)}</b> симв.
                  {" / "}
                  лимит <b>{Number(promptDebug.doc_context_char_limit || 0)}</b>
                </span>
                {promptDebug.outline_context_char_limit != null && (
                  <span>
                    Контекст плана: <b>{Number(promptDebug.outline_context_chars || 0)}</b> симв.
                    {" / "}
                    лимит <b>{Number(promptDebug.outline_context_char_limit || 0)}</b>
                  </span>
                )}
                {promptDebug.approx_prompt_chars != null && (
                  <span>
                    Prompt (оценка): <b>{Number(promptDebug.approx_prompt_chars || 0)}</b> симв.
                  </span>
                )}
                {promptDebug.planned_turns != null && (
                  <span>
                    Плановых ходов: <b>{Number(promptDebug.planned_turns || 0)}</b>
                  </span>
                )}
              </div>
              {promptDebugQuery && (
                <details className="prompt-debug-query">
                  <summary>Поисковый query для RAG</summary>
                  <pre>{promptDebugQuery}</pre>
                </details>
              )}
            </div>
          )}
          <details className="settings-collapsible">
            <summary>Пользовательские сценарии</summary>
            <div className="settings-subgrid">
              <label title="Создать/обновить пользовательский сценарий (структуру диалога)">
                Название сценария
                <input
                  type="text"
                  value={customScenarioName}
                  onChange={(e) => setCustomScenarioName(e.target.value)}
                  placeholder="Например: Разбор продукта"
                />
              </label>
              <label title="Краткое описание структуры сценария. Используется как prompt-guidance.">
                Инструкция сценария
                <input
                  type="text"
                  value={customScenarioInstruction}
                  onChange={(e) => setCustomScenarioInstruction(e.target.value)}
                  placeholder="Ведущий задаёт рамку, гости дают практические кейсы, финал — рекомендации"
                />
              </label>
              <label title="Минимальное количество ролей для сценария">
                Мин. ролей
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={customScenarioMinRoles}
                  onChange={(e) => setCustomScenarioMinRoles(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                />
              </label>
              <label title="Максимальное количество ролей для сценария">
                Макс. ролей
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={customScenarioMaxRoles}
                  onChange={(e) => setCustomScenarioMaxRoles(Math.max(1, Math.min(20, Number(e.target.value) || 8)))}
                />
              </label>
              <div className="action-inline-group">
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    if (!customScenarioName.trim() || !customScenarioInstruction.trim()) {
                      onError("Заполните название и инструкцию сценария.");
                      return;
                    }
                    try {
                      const current = currentScenarioProfile;
                      const minRoles = Math.max(1, Math.min(20, Number(customScenarioMinRoles) || 1));
                      const maxRoles = Math.max(minRoles, Math.min(20, Number(customScenarioMaxRoles) || 8));
                      const res = await upsertScriptScenario({
                        ...(current && !current.is_builtin ? { id: current.id } : {}),
                        name: customScenarioName.trim(),
                        description: customScenarioInstruction.trim(),
                        min_roles: minRoles,
                        max_roles: maxRoles,
                      });
                      const list = (res && res.scenarios) || [];
                      if (Array.isArray(list) && list.length > 0) {
                        const normalized = normalizeScenarioProfiles(list);
                        setScenarioProfiles(normalized);
                        const selectedId = (current && !current.is_builtin ? current.id : null);
                        const savedScenario =
                          (selectedId && normalized.find((x) => x.id === selectedId)) ||
                          normalized.find((x) => x.name === customScenarioName.trim() && !x.is_builtin);
                        if (savedScenario) setScenario(savedScenario.id);
                      }
                    } catch (e) {
                      onError(e.message || "Не удалось сохранить сценарий");
                    }
                  }}
                >
                  {currentScenarioProfile && !currentScenarioProfile.is_builtin ? "Обновить текущий сценарий" : "Сохранить сценарий"}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setCustomScenarioName(currentScenarioProfile?.name || "");
                    setCustomScenarioInstruction(currentScenarioProfile?.hint || "");
                    setCustomScenarioMinRoles(Math.max(1, Number(currentScenarioProfile?.min_roles) || 1));
                    setCustomScenarioMaxRoles(Math.max(1, Number(currentScenarioProfile?.max_roles) || 8));
                  }}
                  disabled={!currentScenarioProfile}
                  title="Подставить поля из текущего выбранного сценария"
                >
                  Подставить из текущего
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    const current = currentScenarioProfile;
                    if (!current || current.is_builtin) {
                      onError("Можно удалить только пользовательский сценарий.");
                      return;
                    }
                    try {
                      const res = await deleteScriptScenario(current.id);
                      const list = (res && res.scenarios) || [];
                      const normalized = normalizeScenarioProfiles(list);
                      setScenarioProfiles(normalized.length ? normalized : FALLBACK_SCENARIO_PROFILES);
                      const fallbackId = (normalized[0] && normalized[0].id) || "classic_overview";
                      setScenario(fallbackId);
                    } catch (e) {
                      onError(e.message || "Не удалось удалить сценарий");
                    }
                  }}
                  disabled={!!currentScenarioProfile?.is_builtin}
                >
                  Удалить текущий сценарий
                </button>
              </div>
            </div>
          </details>
          <label title="Режим генерации: один запрос ко всей сцене или пошаговая генерация по ролям">
            Режим генерации
            <select value={generationMode} onChange={(e) => setGenerationMode(e.target.value)}>
              <option value="single_pass">Один проход</option>
              <option value="turn_taking">Пошагово по ролям</option>
            </select>
            <span className="style-hint">
              {generationMode === "turn_taking"
                ? "Роли отвечают по очереди с учётом истории реплик."
                : "Один запрос генерирует весь скрипт целиком."}
            </span>
          </label>
          {(scenario === "debate" || scenario === "news_digest") && (
            <div className="settings-section-title">Опции выбранного сценария</div>
          )}
          {scenario === "debate" && (
            <>
              <label title="Позиция первого спорящего спикера (обычно второй голос из списка ролей)">
                Позиция A (дебаты)
                <input
                  type="text"
                  value={scenarioDraft.debate_stance_a}
                  onChange={(e) => setScenarioDraft((prev) => ({ ...prev, debate_stance_a: e.target.value }))}
                  placeholder="Скептик"
                />
              </label>
              <label title="Позиция второго спорящего спикера (обычно третий голос из списка ролей)">
                Позиция B (дебаты)
                <input
                  type="text"
                  value={scenarioDraft.debate_stance_b}
                  onChange={(e) => setScenarioDraft((prev) => ({ ...prev, debate_stance_b: e.target.value }))}
                  placeholder="Оптимист"
                />
              </label>
            </>
          )}
          {scenario === "news_digest" && (
            <>
              <label title="Количество коротких блоков в новостном дайджесте">
                Блоков в дайджесте
                <input
                  type="number"
                  min="2"
                  max="12"
                  value={scenarioDraft.news_block_count}
                  onChange={(e) => setScenarioDraft((prev) => ({ ...prev, news_block_count: e.target.value }))}
                />
              </label>
              <label title="Тон новостного дайджеста">
                Тон дайджеста
                <input
                  type="text"
                  value={scenarioDraft.news_tone}
                  onChange={(e) => setScenarioDraft((prev) => ({ ...prev, news_tone: e.target.value }))}
                  placeholder="Нейтральный"
                />
              </label>
            </>
          )}
          <details className="settings-collapsible">
            <summary>Пользовательские шаблоны тона</summary>
            <div className="settings-subgrid">
              <label title="Создать/обновить пользовательский шаблон стиля">
                Название шаблона
                <input
                  type="text"
                  value={customStyleName}
                  onChange={(e) => setCustomStyleName(e.target.value)}
                  placeholder="Например: Product update"
                />
              </label>
              <label title="Инструкция для пользовательского шаблона">
                Инструкция шаблона
                <input
                  type="text"
                  value={customStyleInstruction}
                  onChange={(e) => setCustomStyleInstruction(e.target.value)}
                  placeholder="Короткие тезисы, цифры и конкретные выводы"
                />
              </label>
              <div className="action-inline-group">
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    if (!customStyleName.trim() || !customStyleInstruction.trim()) {
                      onError("Заполните название и инструкцию шаблона.");
                      return;
                    }
                    try {
                      const res = await upsertStyleProfile({
                        name: customStyleName.trim(),
                        instruction: customStyleInstruction.trim(),
                      });
                      const list = (res && res.profiles) || [];
                      if (Array.isArray(list) && list.length > 0) {
                        setStyleProfiles(list.map((x) => ({ id: x.id, name: x.name || x.id, instruction: x.instruction || "" })));
                        setStyle(list[list.length - 1].id);
                      }
                    } catch (e) {
                      onError(e.message || "Не удалось сохранить шаблон стиля");
                    }
                  }}
                >
                  Сохранить шаблон
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    try {
                      const res = await deleteStyleProfile(style);
                      const list = (res && res.profiles) || [];
                      setStyleProfiles(list.map((x) => ({ id: x.id, name: x.name || x.id, instruction: x.instruction || "" })));
                      if (list.length) setStyle(list[0].id);
                    } catch (e) {
                      onError(e.message || "Не удалось удалить шаблон");
                    }
                  }}
                >
                  Удалить текущий стиль
                </button>
              </div>
            </div>
          </details>
          <label title="Генерировать текст с ударениями (+), русской транскрипцией английских слов и без цифр для синтеза речи">
            <input
              type="checkbox"
              checked={ttsFriendly}
              onChange={(e) => setTtsFriendly(e.target.checked)}
            />
            TTS-оптимизация: ударения, транскрипция и числа словами
          </label>
          <details className="settings-collapsible">
            <summary>Качество (дополнительно)</summary>
            <div className="settings-subgrid">
              <div className="action-inline-group">
                <button
                  type="button"
                  className="secondary"
                  disabled={qualityBusy || !ingested}
                  onClick={async () => {
                    if (!documentId) return;
                    setQualityBusy(true);
                    try {
                      const res = await getQualityReport(documentId);
                      setQualityReport(res);
                    } catch (e) {
                      onError(e.message || "Не удалось оценить качество");
                    } finally {
                      setQualityBusy(false);
                    }
                  }}
                >
                  {qualityBusy ? "Оценка…" : "Проверить качество"}
                </button>
                {qualityReport && (
                  <span className="style-hint">
                    Качество: {Math.round((qualityReport.overall_score || 0) * 100)}%
                  </span>
                )}
              </div>
            </div>
          </details>
        </div>
      </details>
    </div>
  );
}
