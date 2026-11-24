"""AI Chatbot, Idle Helper & Unused Program Hunter for AstraCradle.

Scans local processes and startup/installed apps (only with user consent)
to pinpoint dormant AI chatbot runtimes, unused background helpers, and
bloatware, providing clear 1-click turn-off or automation recommendations.
"""

from __future__ import annotations

from .config import AppConfig
from .models import ProcessInfo, ProcessRecommendation, StartupItem


AI_CHATBOT_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("ollama", "Ollama local LLM server"),
    ("lmstudio", "LM Studio local AI runner"),
    ("lm-studio", "LM Studio local AI runner"),
    ("gpt4all", "GPT4All local chatbot"),
    ("koboldcpp", "KoboldCpp local LLM runtime"),
    ("anythingllm", "AnythingLLM desktop assistant"),
    ("open-webui", "OpenWebUI AI chat frontend"),
    ("openwebui", "OpenWebUI AI chat frontend"),
    ("text-generation-webui", "Oobabooga text-generation-webui"),
    ("llama-server", "llama.cpp server process"),
    ("llamafile", "Llamafile local AI server"),
    ("localai", "LocalAI inference server"),
    ("jan", "Jan local AI assistant"),
    ("copilot", "AI Copilot background helper"),
    ("chatgpt", "ChatGPT desktop assistant"),
    ("claude", "Claude desktop helper"),
    ("perplexity", "Perplexity desktop assistant"),
    ("tabby", "Tabby AI code assistant"),
    ("vllm", "vLLM inference worker"),
)

IDLE_HELPER_SIGNATURES: tuple[str, ...] = (
    "updater",
    "update_service",
    "telemetry",
    "crashpad",
    "helper",
    "agent",
    "tray",
    "sync",
    "onedrive",
    "dropbox",
    "teams",
    "discord",
    "slack",
    "spotify",
    "adobe",
    "armoury",
    "razer",
    "icue",
)


class ProcessAdvisor:
    """Identifies AI chatbot daemons, idle background helpers, and unused startup programs."""

    @staticmethod
    def classify_process(proc: ProcessInfo) -> ProcessInfo:
        hay = f"{proc.name} {proc.cmdline}".lower()
        for sig, label in AI_CHATBOT_SIGNATURES:
            if sig in hay:
                proc.category = "ai_chatbot"
                if proc.cpu_percent < 2.0:
                    proc.recommendation = f"Idle AI chatbot/runtime ({label}) using {proc.memory_mb:.0f} MB RAM"
                else:
                    proc.recommendation = f"Active AI runtime ({label})"
                return proc

        if proc.protected:
            proc.category = "system"
            return proc

        if proc.cpu_percent < 0.3 and proc.memory_mb >= 45.0:
            if any(h in hay for h in IDLE_HELPER_SIGNATURES):
                proc.category = "idle_helper"
                proc.recommendation = f"Dormant background helper holding {proc.memory_mb:.0f} MB RAM at ~0% CPU"
                return proc
        return proc

    def analyze(
        self,
        processes: list[ProcessInfo],
        startup_items: list[StartupItem],
        config: AppConfig,
    ) -> list[ProcessRecommendation]:
        if not config.consent_process_scan:
            return []

        recs: list[ProcessRecommendation] = []
        ignored = set(config.ignored_recommendations)

        for p in processes:
            self.classify_process(p)
            if p.protected:
                continue
            key = f"proc:{p.name.lower()}"
            if key in ignored:
                continue

            if p.category == "ai_chatbot":
                is_idle = p.cpu_percent < 2.5
                recs.append(
                    ProcessRecommendation(
                        rec_id=f"ai-{p.pid}",
                        category="ai_chatbot",
                        name=f"{p.name} (PID {p.pid})",
                        pid=p.pid,
                        memory_mb=p.memory_mb,
                        cpu_percent=p.cpu_percent,
                        confidence="high" if is_idle else "medium",
                        reason=(
                            f"Detected local AI chatbot/LLM process ({p.recommendation or p.name}) "
                            f"holding {p.memory_mb:.1f} MB RAM while {'idle' if is_idle else 'running in background'}."
                        ),
                        suggested_action="terminate",
                        target_ref=p.name,
                        safe_to_automate=is_idle,
                    )
                )
            elif p.category == "idle_helper":
                recs.append(
                    ProcessRecommendation(
                        rec_id=f"idle-{p.pid}",
                        category="idle_process",
                        name=f"{p.name} (PID {p.pid})",
                        pid=p.pid,
                        memory_mb=p.memory_mb,
                        cpu_percent=p.cpu_percent,
                        confidence="medium",
                        reason=(
                            f"Idle background helper consuming {p.memory_mb:.1f} MB RAM with {p.cpu_percent:.1f}% CPU activity."
                        ),
                        suggested_action="terminate",
                        target_ref=p.name,
                        safe_to_automate=False,
                    )
                )

        # Also inspect startup entries for AI chatbots / heavy auto-launchers
        for st in startup_items:
            if not st.enabled:
                continue
            low = f"{st.name} {st.command}".lower()
            if any(sig in low for sig, _ in AI_CHATBOT_SIGNATURES) or any(h in low for h in IDLE_HELPER_SIGNATURES):
                rec_key = f"startup:{st.item_id}"
                if rec_key in ignored:
                    continue
                is_ai = any(sig in low for sig, _ in AI_CHATBOT_SIGNATURES)
                recs.append(
                    ProcessRecommendation(
                        rec_id=rec_key,
                        category="ai_chatbot" if is_ai else "startup_bloat",
                        name=f"Startup: {st.name}",
                        pid=None,
                        memory_mb=0.0,
                        cpu_percent=0.0,
                        confidence="high" if is_ai else "medium",
                        reason=(
                            f"Auto-launches at login from {st.location}; disabling stops unwanted background load."
                        ),
                        suggested_action="disable_startup",
                        target_ref=st.location,
                        safe_to_automate=False,
                    )
                )

        recs.sort(key=lambda r: (0 if r.category == "ai_chatbot" else 1, -r.memory_mb))
        return recs
