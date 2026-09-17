"""Venice AI client (OpenAI-compatible chat API) — the "intelligent" half.

Two jobs:

* :meth:`Venice.analyze_project` — given the static report and a few file
  excerpts, decide the entry point, GUI vs console, product name/description,
  PyInstaller hidden imports / collect-all / data files, and flag things that
  will break once frozen.
* :meth:`Venice.diagnose_build` — read a failed PyInstaller log (or a crash
  from running the result) and return concrete fixes the GUI can apply.

Only ``urllib`` is used so the maker has no HTTP dependency. The API key is
never logged.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

BASE_URL = "https://api.venice.ai/api/v1"
# Venice's 'default_code' model at the time of writing; the live list wins.
FALLBACK_MODEL = "deepseek-v4-pro-0813"
FALLBACK_MODELS = [FALLBACK_MODEL, "qwen3-coder-480b-a35b-instruct-turbo", "claude-sonnet-5",
                   "kimi-k2-7-code", "zai-org-glm-5-2", "llama-3.3-70b"]


class VeniceError(Exception):
    pass


class Venice:
    def __init__(self, api_key: str, model: str = "", base_url: str = BASE_URL, timeout: int = 180):
        if not api_key:
            raise VeniceError("No Venice API key configured (Settings tab, or VENICE_API_KEY).")
        self.api_key = api_key
        self.model = model or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ───────────────────────────── transport ─────────────────────────────

    def _request(self, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "User-Agent": "decint-exe-maker/1.0"},
            method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode(errors="replace")[:400]
            if ex.code == 401:
                raise VeniceError("Venice rejected the API key (401). Check Settings.") from None
            raise VeniceError(f"Venice HTTP {ex.code}: {detail}") from None
        except urllib.error.URLError as ex:
            raise VeniceError(f"Cannot reach Venice: {ex.reason}") from None
        except TimeoutError:
            raise VeniceError("Venice request timed out.") from None

    def models(self) -> list[dict]:
        """``[{id, traits, code}]`` for text models, code-optimised ones first."""
        data = self._request("/models?type=text").get("data", [])
        out = []
        for m in data:
            spec = m.get("model_spec", {}) or {}
            caps = spec.get("capabilities", {}) or {}
            out.append({"id": m["id"], "traits": spec.get("traits") or [],
                        "code": bool(caps.get("optimizedForCode")),
                        "json": bool(caps.get("supportsResponseSchema"))})
        out.sort(key=lambda m: ("default_code" not in m["traits"], not m["code"], m["id"]))
        return out

    def pick_model(self) -> str:
        if self.model:
            return self.model
        try:
            for m in self.models():
                if "default_code" in m["traits"]:
                    self.model = m["id"]
                    return self.model
        except VeniceError:
            pass
        self.model = FALLBACK_MODEL
        return self.model

    def chat(self, messages: list[dict], *, json_schema: dict | None = None,
             temperature: float = 0.15, max_tokens: int = 4000) -> str:
        body = {"model": self.pick_model(), "messages": messages, "temperature": temperature,
                "max_completion_tokens": max_tokens,
                "venice_parameters": {"include_venice_system_prompt": False}}
        if json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "schema": json_schema}}
        data = self._request("/chat/completions", body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise VeniceError(f"Unexpected Venice response: {str(data)[:300]}")
        if isinstance(content, list):   # some models return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content or ""

    def chat_json(self, messages: list[dict], schema: dict, **kw) -> dict:
        text = self.chat(messages, json_schema=schema, **kw)
        return _extract_json(text)

    # ───────────────────────────── tasks ─────────────────────────────

    ANALYSIS_SCHEMA = {
        "type": "object",
        "properties": {
            "entry_point": {"type": "string"},
            "app_type": {"type": "string", "enum": ["gui", "console"]},
            "product_name": {"type": "string"},
            "description": {"type": "string"},
            "version": {"type": "string"},
            "pip_requirements": {"type": "array", "items": {"type": "string"}},
            "hidden_imports": {"type": "array", "items": {"type": "string"}},
            "collect_all": {"type": "array", "items": {"type": "string"}},
            "data_files": {"type": "array", "items": {"type": "string"}},
            "exclude_modules": {"type": "array", "items": {"type": "string"}},
            "chdir_to_bundle": {"type": "boolean"},
            "risks": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
        "required": ["entry_point", "app_type", "product_name", "description", "version",
                     "pip_requirements", "hidden_imports", "collect_all", "data_files",
                     "exclude_modules", "chdir_to_bundle", "risks", "notes"],
        "additionalProperties": False,
    }

    def analyze_project(self, static_report: dict, excerpts: dict[str, str]) -> dict:
        system = (
            "You are a senior Python packaging engineer. A tool is about to freeze a Python project "
            "into a single Windows EXE with PyInstaller (onefile) and wrap it in a license check. "
            "Given a static analysis report and excerpts of the most relevant files, decide the build "
            "configuration. Be precise and conservative: only list hidden imports / collect-all entries "
            "for packages that genuinely need them when frozen (e.g. customtkinter, ttkbootstrap, "
            "pyttsx3 drivers, uvicorn loops, sklearn internals, dynamic plugin imports you can see in the "
            "code). data_files must be project-relative paths of files or folders the app opens at "
            "runtime (images, json, db, models) — not docs or tests. Set chdir_to_bundle=true only if "
            "the code opens data files with paths relative to the working directory rather than "
            "__file__. pip_requirements are distribution names (pillow, not PIL). product_name is a "
            "short human-friendly product title; description is one sentence for the EXE's file "
            "properties; version is dotted numeric. risks: concrete things likely to break once frozen "
            "(e.g. 'uses __file__ to find data/', 'spawns python subprocess', 'multiprocessing without "
            "freeze_support'). Return JSON only."
        )
        parts = [f"STATIC REPORT:\n{json.dumps(static_report, indent=1)[:12000]}"]
        for name, text in excerpts.items():
            parts.append(f"\n===== {name} =====\n{text}")
        result = self.chat_json([{"role": "system", "content": system},
                                 {"role": "user", "content": "\n".join(parts)[:60000]}],
                                self.ANALYSIS_SCHEMA)
        return _sanitize_analysis(result, static_report)

    DIAGNOSIS_SCHEMA = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "fixes": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["pip_install", "hidden_import", "collect_all",
                                                        "exclude_module", "data_file", "app_type",
                                                        "chdir_to_bundle", "onedir", "manual"]},
                    "value": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["type", "value", "why"], "additionalProperties": False}},
        },
        "required": ["summary", "fixes"], "additionalProperties": False,
    }

    def diagnose_build(self, log_tail: str, config: dict) -> dict:
        system = (
            "You are a PyInstaller expert. A build (or the first run of the built EXE) failed. Read the "
            "log and the build configuration and return: a two-sentence plain-English summary of the "
            "root cause, and a list of concrete fixes. Fix types: pip_install (value = package to "
            "install into the build interpreter), hidden_import (value = module), collect_all "
            "(value = package), exclude_module (value = module), data_file (value = project-relative "
            "path to bundle), app_type (value = gui|console), chdir_to_bundle (value = true), onedir "
            "(value = true, when onefile extraction is the problem), manual (value = what the user must "
            "change in their code, e.g. add multiprocessing.freeze_support()). Prefer the smallest set "
            "of fixes that would make the build succeed. Return JSON only."
        )
        user = f"BUILD CONFIG:\n{json.dumps(config, indent=1)[:4000]}\n\nLOG (tail):\n{log_tail[-14000:]}"
        return self.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                              self.DIAGNOSIS_SCHEMA, max_tokens=2500)


# ───────────────────────────── helpers ─────────────────────────────

def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise VeniceError(f"Model did not return JSON: {text[:200]}")


def _sanitize_analysis(r: dict, static: dict) -> dict:
    """Never let a hallucinated path or type leak into the build config."""
    files = set(static.get("python_files", []))
    entry = (r.get("entry_point") or "").replace("\\", "/").lstrip("./")
    if entry not in files:
        entry = static.get("entry") or (static.get("entry_candidates") or [None])[0]
    r["entry_point"] = entry
    r["app_type"] = r.get("app_type") if r.get("app_type") in ("gui", "console") else static.get("app_type")
    for key in ("pip_requirements", "hidden_imports", "collect_all", "data_files", "exclude_modules", "risks"):
        vals = r.get(key) or []
        r[key] = [str(v).strip() for v in vals if str(v).strip()] if isinstance(vals, list) else []
    r["product_name"] = (r.get("product_name") or static.get("suggested_name") or "My App").strip()[:60]
    r["description"] = (r.get("description") or "").strip()[:200]
    v = (r.get("version") or "").strip()
    r["version"] = v if re.fullmatch(r"\d+(\.\d+){0,3}", v) else static.get("suggested_version", "1.0.0")
    r["chdir_to_bundle"] = bool(r.get("chdir_to_bundle"))
    r["notes"] = (r.get("notes") or "").strip()
    return r
