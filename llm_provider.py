"""Provider abstraction for IIPP tool-using chat and recommendations.

Conversation history and tool calls use a provider-neutral representation. Bedrock
is the default. Optional OpenAI, Anthropic, Gemini and OpenAI-compatible local
providers require their own credentials/endpoint and are never silently selected.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any
from botocore.config import Config


def _secret_or_env(env_name: str, secret_path: str) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    try:
        with open(secret_path, encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return ""


def _http_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            body_text = ""
        # Provider bodies sometimes echo request details. Redact configured secrets
        # before exposing diagnostics to a dashboard user.
        safe_detail = body_text or str(exc.reason)
        secret_values = []
        for key_name, value in os.environ.items():
            if ("KEY" in key_name.upper() or "TOKEN" in key_name.upper() or "SECRET" in key_name.upper()) and value:
                secret_values.append(value)
        # Optional provider credentials are mounted as files, so include those values
        # in redaction too (including the reviewer token if a provider echoes a request).
        for secret_path in (
            "/run/secrets/openai-api-key", "/run/secrets/anthropic-api-key",
            "/run/secrets/gemini-api-key", "/run/secrets/local-llm-api-key",
            "/run/secrets/google-cloud-billing-api-key", "/run/secrets/cloudpilot-approval-token",
        ):
            try:
                value = open(secret_path, encoding="utf-8").read().strip()
                if value:
                    secret_values.append(value)
            except OSError:
                pass
        for value in secret_values:
            for variant in {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)}:
                safe_detail = safe_detail.replace(variant, "[REDACTED]")
        safe_detail = safe_detail[:500]
        raise RuntimeError(f"LLM provider returned HTTP {exc.code}: {safe_detail or 'request rejected'}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the configured LLM provider: {exc.reason}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("LLM provider returned a non-object JSON response.")
    return result


def _tool_defs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in tools:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        normalized.append({
            "name": str(item["name"]),
            "description": str(item.get("description", "")),
            "parameters": item.get("parameters") if isinstance(item.get("parameters"), dict) else {"type": "object", "properties": {}},
        })
    return normalized


def _native_messages(history: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Convert neutral conversation records to OpenAI-compatible messages."""
    messages: list[dict[str, Any]] = []
    for item in history:
        role = item.get("role")
        if role == "user":
            messages.append({"role": "user", "content": str(item.get("text", ""))})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": item.get("text") or None}
            calls = item.get("tool_calls", [])
            if calls:
                msg["tool_calls"] = [{
                    "id": call.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}), separators=(",", ":"))},
                } for call in calls]
            messages.append(msg)
        elif role == "tool":
            messages.append({"role": "tool", "tool_call_id": item.get("tool_call_id", ""),
                             "name": item.get("name", ""), "content": json.dumps(item.get("result", {}), default=str)})
    return messages


def _parse_openai_response(response: dict[str, Any], provider: str, model: str, region: str | None = None) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError(f"{provider} returned no response choices.")
    message = choices[0].get("message", {})
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{provider} returned invalid JSON arguments for tool {function.get('name', 'unknown')}.") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError(f"{provider} tool arguments for {function.get('name', 'unknown')} must be a JSON object.")
        calls.append({"id": call.get("id") or f"call_{uuid.uuid4().hex[:16]}", "name": function.get("name", ""), "arguments": arguments})
    return {"text": message.get("content") or "", "tool_calls": calls,
            "provider": provider, "model_id": model, "region": region,
            "usage": response.get("usage", {}), "stop_reason": choices[0].get("finish_reason")}


def _openai_compatible(history: list[dict[str, Any]], system: str, tools: list[dict[str, Any]], max_tokens: int,
                       temperature: float, provider_name: str, api_key: str | None, base_url: str, model: str,
                       timeout: int = 60) -> dict[str, Any]:
    if api_key is None and provider_name != "Local OpenAI-compatible":
        raise RuntimeError(f"{provider_name} is selected but its API key is not configured.")
    if not base_url.startswith(("https://", "http://")):
        raise RuntimeError("LLM API base URL must start with http:// or https://.")
    messages = [{"role": "system", "content": system}, *_native_messages(history, "openai")]
    payload: dict[str, Any] = {
        "model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        payload["tool_choice"] = "auto"
    headers = {"Accept": "application/json", "User-Agent": "Innominds-IIPP/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    response = _http_json(endpoint, headers, payload, timeout)
    return _parse_openai_response(response, provider_name, model)


def _anthropic(history: list[dict[str, Any]], system: str, tools: list[dict[str, Any]], max_tokens: int,
               temperature: float) -> dict[str, Any]:
    api_key = _secret_or_env("ANTHROPIC_API_KEY", "/run/secrets/anthropic-api-key")
    if not api_key:
        raise RuntimeError("ANTHROPIC is selected but ANTHROPIC_API_KEY is not configured.")
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    messages: list[dict[str, Any]] = []
    tool_ids: dict[str, str] = {}
    for item in history:
        if item.get("role") in {"user", "assistant"}:
            content: list[dict[str, Any]] = []
            if item.get("text"):
                content.append({"type": "text", "text": str(item["text"])})
            if item.get("role") == "assistant":
                for call in item.get("tool_calls", []):
                    call_id = call.get("id") or f"toolu_{uuid.uuid4().hex[:16]}"
                    tool_ids[call_id] = call["name"]
                    content.append({"type": "tool_use", "id": call_id, "name": call["name"], "input": call.get("arguments", {})})
            messages.append({"role": item["role"], "content": content or [{"type": "text", "text": " "}]})
        elif item.get("role") == "tool":
            block = {"type": "tool_result", "tool_use_id": item.get("tool_call_id", ""),
                     "content": json.dumps(item.get("result", {}), default=str), "is_error": item.get("status") == "error"}
            if messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), list):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
    payload = {
        "model": model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
        "messages": messages,
    }
    if tools:
        payload["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    response = _http_json("https://api.anthropic.com/v1/messages", {
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "User-Agent": "Innominds-IIPP/1.0"
    }, payload)
    text_chunks, calls = [], []
    for block in response.get("content", []):
        if block.get("type") == "text":
            text_chunks.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            arguments = block.get("input", {})
            if not isinstance(arguments, dict):
                raise RuntimeError("Anthropic returned non-object tool arguments.")
            calls.append({"id": block.get("id") or f"toolu_{uuid.uuid4().hex[:16]}", "name": block.get("name", ""), "arguments": arguments})
    return {"text": "\n\n".join(x for x in text_chunks if x), "tool_calls": calls,
            "provider": "Anthropic Claude", "model_id": model, "usage": response.get("usage", {}),
            "stop_reason": response.get("stop_reason")}


def _gemini(history: list[dict[str, Any]], system: str, tools: list[dict[str, Any]], max_tokens: int,
            temperature: float) -> dict[str, Any]:
    api_key = _secret_or_env("GEMINI_API_KEY", "/run/secrets/gemini-api-key")
    if not api_key:
        raise RuntimeError("GEMINI is selected but GEMINI_API_KEY is not configured.")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    contents: list[dict[str, Any]] = []
    for item in history:
        role = item.get("role")
        if role in {"user", "assistant"}:
            parts: list[dict[str, Any]] = []
            if item.get("text"):
                parts.append({"text": str(item["text"])})
            if role == "assistant":
                for call in item.get("tool_calls", []):
                    parts.append({"functionCall": {"name": call["name"], "args": call.get("arguments", {})}})
            contents.append({"role": "user" if role == "user" else "model", "parts": parts or [{"text": " "}]})
        elif role == "tool":
            contents.append({"role": "user", "parts": [{"functionResponse": {
                "name": item.get("name", ""), "response": item.get("result", {})
            }}]})
    payload = {
        "systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if tools:
        payload["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in tools]}]
        payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?" + urllib.parse.urlencode({"key": api_key})
    response = _http_json(url, {"User-Agent": "Innominds-IIPP/1.0"}, payload)
    candidates = response.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no response candidates.")
    text_chunks, calls = [], []
    for part in candidates[0].get("content", {}).get("parts", []):
        if part.get("text"):
            text_chunks.append(part["text"])
        function_call = part.get("functionCall")
        if function_call:
            args = function_call.get("args", {})
            if not isinstance(args, dict):
                raise RuntimeError("Gemini returned non-object tool arguments.")
            calls.append({"id": f"gemini_{uuid.uuid4().hex[:16]}", "name": function_call.get("name", ""), "arguments": args})
    return {"text": "\n\n".join(text_chunks), "tool_calls": calls, "provider": "Google Gemini",
            "model_id": model, "usage": response.get("usageMetadata", {}),
            "stop_reason": candidates[0].get("finishReason")}


def complete_turn(history: list[dict[str, Any]], system: str, tool_specs: list[dict[str, Any]],
                  max_tokens: int = 1800, temperature: float = 0.2) -> dict[str, Any]:
    """Request one model turn, returning normalized text and validated tool-call objects."""
    provider = os.getenv("LLM_PROVIDER", "bedrock").strip().lower()
    tools = _tool_defs(tool_specs)
    if provider in {"openai", "local"}:
        if provider == "openai":
            api_key = _secret_or_env("OPENAI_API_KEY", "/run/secrets/openai-api-key")
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            label = "OpenAI"
        else:
            api_key = _secret_or_env("LOCAL_LLM_API_KEY", "/run/secrets/local-llm-api-key")
            base = os.getenv("LOCAL_LLM_BASE_URL", "")
            model = os.getenv("LOCAL_LLM_MODEL", "local-model")
            label = "Local OpenAI-compatible"
            if not base:
                raise RuntimeError("LLM_PROVIDER=local requires LOCAL_LLM_BASE_URL (an OpenAI-compatible /v1 endpoint).")
        return _openai_compatible(history, system, tools, max_tokens, temperature, label,
                                  api_key or None, base, model)
    if provider == "anthropic":
        return _anthropic(history, system, tools, max_tokens, temperature)
    if provider in {"gemini", "google"}:
        return _gemini(history, system, tools, max_tokens, temperature)
    if provider != "bedrock":
        raise RuntimeError("Unsupported LLM_PROVIDER. Choose bedrock, openai, anthropic, gemini, or local.")

    import boto3
    region = os.getenv("AWS_REGION", "ap-south-1")
    model = os.getenv("BEDROCK_MODEL_ID", "apac.amazon.nova-micro-v1:0")
    client = boto3.client("bedrock-runtime", region_name=region, config=Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 2, "mode": "standard"}))
    bedrock_messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []
    for item in history:
        role = item.get("role")
        if role == "user":
            if pending_tool_results:
                bedrock_messages.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []
            bedrock_messages.append({"role": "user", "content": [{"text": str(item.get("text", ""))}]})
        elif role == "assistant":
            if pending_tool_results:
                bedrock_messages.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []
            content = []
            if item.get("text"):
                content.append({"text": str(item["text"])})
            for call in item.get("tool_calls", []):
                content.append({"toolUse": {"toolUseId": call.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                                             "name": call["name"], "input": call.get("arguments", {})}})
            bedrock_messages.append({"role": "assistant", "content": content or [{"text": " "}]})
        elif role == "tool":
            pending_tool_results.append({"toolResult": {
                "toolUseId": item.get("tool_call_id", ""),
                "content": [{"json": item.get("result", {})}],
                "status": "error" if item.get("status") == "error" else "success",
            }})
    if pending_tool_results:
        bedrock_messages.append({"role": "user", "content": pending_tool_results})
    converse_args: dict[str, Any] = {
        "modelId": model,
        "system": [{"text": system}],
        "messages": bedrock_messages,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if tools:
        converse_args["toolConfig"] = {"tools": [{"toolSpec": {
            "name": t["name"], "description": t["description"],
            "inputSchema": {"json": t["parameters"]},
        }} for t in tools]}
    response = client.converse(**converse_args)
    content = response.get("output", {}).get("message", {}).get("content", [])
    text_parts, calls = [], []
    for block in content:
        if block.get("text"):
            text_parts.append(block["text"])
        call = block.get("toolUse")
        if call:
            args = call.get("input", {})
            if not isinstance(args, dict):
                raise RuntimeError(f"Bedrock returned non-object arguments for tool {call.get('name', 'unknown')}.")
            calls.append({"id": call.get("toolUseId") or f"bedrock_{uuid.uuid4().hex[:16]}",
                          "name": call.get("name", ""), "arguments": args})
    return {"text": "\n\n".join(text_parts), "tool_calls": calls, "provider": "Amazon Bedrock",
            "model_id": model, "region": region, "usage": response.get("usage", {}),
            "stop_reason": response.get("stopReason")}
