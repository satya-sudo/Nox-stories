from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

from .files import write_json


class Ollama:
    def __init__(self, model, audit_folder, check_control=lambda: None):
        self.model = model
        self.audit_folder = audit_folder
        self.check_control = check_control
        self.supports_thinking = False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def api(self, endpoint, data=None):
        request = urllib.request.Request("http://127.0.0.1:11434/api/" + endpoint,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=900) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f"Ollama HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise RuntimeError("Ollama is unavailable; start the local server and resume the episode")
            self.check_control()
            time.sleep(2 ** attempt)

    def identity(self):
        for model in self.api("tags")["models"]:
            if model["name"] == self.model:
                self.supports_thinking = "thinking" in model.get("capabilities", [])
                return {"name": self.model, "digest": model["digest"]}
        raise RuntimeError(f"Local model {self.model} is missing; download it with ollama pull")

    def structured(self, schema, system, prompt, *, seed=42, tokens=1200, temperature=0.3):
        repair = ""
        for attempt in range(3):
            self.check_control()
            payload = {"model": self.model, "system": system,
                       "prompt": prompt + repair, "format": schema.model_json_schema(),
                       "stream": False, "keep_alive": "2m",
                       "options": {"num_ctx": 4096, "num_batch": 128,
                                   "num_predict": tokens, "temperature": temperature, "seed": seed + attempt,
                                   "repeat_penalty": 1.12, "repeat_last_n": 512}}
            if self.supports_thinking:
                payload["think"] = False
            started = time.monotonic()
            response = self.api("generate", payload)
            write_json(self.audit_folder / (uuid.uuid4().hex + ".json"), {
                "request": payload, "response": {k: v for k, v in response.items() if k != "context"},
                "wall_seconds": time.monotonic() - started,
            })
            self.check_control()
            try:
                if response.get("done_reason") == "length":
                    raise ValueError("Output hit its token limit; write more concisely")
                return schema.model_validate_json(response["response"])
            except (ValueError, KeyError) as exc:
                repair = "\nCorrect the previous formatting/validation error: " + str(exc)[:500]
        raise RuntimeError(f"Structured model response failed validation after three attempts: {repair}")

    def unload(self):
        self.api("generate", {"model": self.model, "keep_alive": 0})

    def text(self, system, prompt, *, seed=42, tokens=1400, temperature=0.7):
        repair = ""
        for attempt in range(3):
            self.check_control()
            payload = {"model": self.model, "system": system, "prompt": prompt + repair,
                       "stream": False, "keep_alive": "2m",
                       "options": {"num_ctx": 4096, "num_batch": 128, "num_predict": tokens,
                                   "temperature": temperature, "seed": seed + attempt,
                                   "repeat_penalty": 1.12, "repeat_last_n": 512}}
            if self.supports_thinking:
                payload["think"] = False
            started = time.monotonic()
            response = self.api("generate", payload)
            write_json(self.audit_folder / (uuid.uuid4().hex + ".json"), {
                "request": payload, "response": {k: v for k, v in response.items() if k != "context"},
                "wall_seconds": time.monotonic() - started,
            })
            self.check_control()
            text = response.get("response", "").strip()
            if text and response.get("done_reason") != "length":
                return text
            repair = "\nReturn a complete scene within the token budget, without a preface or notes."
        raise RuntimeError("Prose was empty or token-truncated after three attempts")
