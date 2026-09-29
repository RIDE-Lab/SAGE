"""Loopback-only provider adapter; all inference is performed by real upstreams."""

import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 19080
ROLES = ("llm", "embedding", "reranker")
CONFIG = {}
OBSERVED = {}


def request_upstream(role, payload=None, suffix=""):
    config = CONFIG[role]
    if not config["endpoint"]:
        raise ValueError(f"{role} is not configured")
    url = config["endpoint"].rstrip("/") + suffix
    headers = {"Content-Type": "application/json"}
    if config["key"]:
        headers["Authorization"] = "Bearer " + config["key"]
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
            data = json.load(response)
        if data.get("success") is False or data.get("error"):
            raise ValueError("Upstream rejected request")
        OBSERVED[role] = {
            "status": "reachable",
            "last_verified_at": time.time(),
            "last_request_seconds": round(time.monotonic() - started, 3),
        }
        return data
    except Exception:
        OBSERVED[role] = {"status": "unreachable", "last_verified_at": time.time()}
        raise


def infer(role, payload):
    config = CONFIG[role]
    if config["provider"] != "cloudflare":
        suffix = {"llm": "/chat/completions", "embedding": "/embeddings", "reranker": "/rerank"}[
            role
        ]
        return request_upstream(role, payload, suffix)
    if role == "embedding":
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        if not inputs or any(not isinstance(x, str) for x in inputs):
            raise ValueError("input must be non-empty text or a list of texts")
        data = request_upstream(role, {"text": inputs})["result"]
        vectors = data["data"]
        if len(vectors) != len(inputs) or any(not isinstance(v, list) or not v for v in vectors):
            raise ValueError("Invalid upstream embedding dimensions or count")
        return {
            "object": "list",
            "model": config["model"],
            "data": [
                {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
            ],
            "usage": data.get("usage", {}),
        }
    documents = payload["documents"]
    texts = [x if isinstance(x, str) else x["text"] for x in documents]
    if not texts:
        raise ValueError("documents must not be empty")
    data = request_upstream(
        role, {"query": payload["query"], "contexts": [{"text": x} for x in texts]}
    )["result"]
    ranked = sorted(data["response"], key=lambda x: x["score"], reverse=True)
    results = [
        {
            "index": int(x["id"]),
            "relevance_score": x["score"],
            "document": {"text": texts[int(x["id"])]},
        }
        for x in ranked
    ]
    return {
        "model": config["model"],
        "results": results[: int(payload.get("top_n", len(results)))],
        "usage": data.get("usage", {}),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 3 or parts[0] not in ROLES or parts[1] != "v1":
            return self.respond(404, {"error": "Unknown model route"})
        role, _, operation = parts
        config = CONFIG[role]
        try:
            if self.command == "GET" and operation == "models":
                if role == "llm":
                    return self.respond(200, request_upstream(role, suffix="/models"))
                return self.respond(
                    200,
                    {
                        "data": [{"id": config["model"]}],
                        "note": "Configured model only; successful inference is tracked separately.",
                    },
                )
            allowed = {"llm": "chat", "embedding": "embeddings", "reranker": "rerank"}
            # Chat completions has one additional path component.
            if self.command != "POST" or operation != allowed[role]:
                return self.respond(404, {"error": "Unknown operation"})
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                return self.respond(400, {"error": "Invalid request size"})
            payload = json.loads(self.rfile.read(length))
            self.respond(200, infer(role, payload))
        except urllib.error.HTTPError as exc:
            OBSERVED[role] = {"status": "unreachable", "last_verified_at": time.time()}
            self.respond(
                502, {"error": f"Upstream HTTP {exc.code}; check credentials, model and quota"}
            )
        except Exception as exc:
            OBSERVED[role] = {"status": "unreachable", "last_verified_at": time.time()}
            message = str(exc)
            for value in CONFIG.values():
                if value["key"]:
                    message = message.replace(value["key"], "[REDACTED]")
            self.respond(502, {"error": message[:250]})

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.path = self.path.replace("/chat/completions", "/chat")
        self.handle_request()


def main():
    for role in ROLES:
        prefix = "SAGE_" + role.upper()
        CONFIG[role] = {
            "endpoint": os.getenv(prefix + "_BASE_URL", ""),
            "model": os.getenv(prefix + "_MODEL", ""),
            "provider": os.getenv(prefix + "_PROVIDER", "openai"),
            "key": os.getenv("SAGE_OPENAI_API_KEY" if role == "llm" else prefix + "_API_KEY", ""),
            "timeout": float(os.getenv("SAGE_MODEL_TIMEOUT", "90")),
        }
        if CONFIG[role]["endpoint"]:
            os.environ[prefix + "_BASE_URL"] = f"http://127.0.0.1:{PORT}/{role}/v1"
    os.environ["SAGE_OPENAI_BASE_URL"] = os.getenv("SAGE_LLM_BASE_URL", "")
    os.environ["SAGE_OPENAI_MODEL"] = os.getenv("SAGE_LLM_MODEL", "")
    os.environ.setdefault("SAGE_EMBEDDING_MODEL_NAME", os.getenv("SAGE_EMBEDDING_MODEL", ""))
    Path("/data/opc").mkdir(parents=True, exist_ok=True)
    Path("/data/logs").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    spec = importlib.util.spec_from_file_location(
        "opc_ui_backend", "/opt/sage/sage-opc-ui/backend/opc_ui_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.build_app(
        workspace_root="/opt/sage/sage-examples", python_executable=sys.executable
    )
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) != "/api/model-services"
    ]

    @app.get("/api/model-services")
    def model_services():
        services = {}
        for role, config in CONFIG.items():
            entry = {
                "endpoint": config["endpoint"],
                "status": "configured" if config["endpoint"] else "not-configured",
                "models": [config["model"]] if config["model"] else [],
                "provider": config["provider"],
                "note": "Status comes from the last real request; configured does not mean verified.",
            }
            entry.update(OBSERVED.get(role, {}))
            services[role] = entry
        return {
            "services": services,
            "offline_demo": "Ticket Triage, Supply Chain Alert and Data Cleaner",
        }

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=18400)


if __name__ == "__main__":
    main()
