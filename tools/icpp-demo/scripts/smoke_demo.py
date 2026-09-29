"""Run real catalog, app, output and operator-metric checks in a fresh container."""

import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18400"


def request(path, payload=None):
    req = urllib.request.Request(
        BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        if "json" in response.headers.get("Content-Type", ""):
            return json.load(response)
        return response.read().decode()


def defaults(app):
    args = []
    for arg in app.get("arguments", []):
        if arg["primary_name"] in ("--host", "--port", "--storage-path"):
            continue
        value = arg.get("opc_default_value")
        if value is not None and arg.get("kind") != "flag":
            if not arg.get("positional"):
                args.append(arg["primary_name"])
            args.append(str(value))
    return args


def main():
    request("/api/health")
    assert "<html" in request("/ui/").lower()
    assert "location.origin" in request("/ui/config.js")
    request("/ui/app.js")
    apps = request("/api/apps")["apps"]
    assert len(apps) > 0
    report = {"app_count": len(apps), "applications": {}}
    for ident in ("ticket_triage_api", "supply_chain_alert_api", "data_cleaner"):
        detail = request("/api/apps/" + ident)
        args = defaults(detail)
        if ident == "data_cleaner":
            folder = Path("/data/verification")
            folder.mkdir(exist_ok=True)
            (folder / "cleaner-input.csv").write_text(
                "name,age,city\nAlice,30,Beijing\nBob,25,Shanghai\nAlice,30,Beijing\n"
            )
            args = [
                "--input",
                str(folder / "cleaner-input.csv"),
                "--output",
                str(folder / "cleaner-output.json"),
                "--type-rules",
                "age:int",
                "--key-fields",
                "name,age,city",
                "--output-format",
                "json",
            ]
        launched = request(
            "/api/apps/" + ident + "/launch",
            {"extra_args": args, "startup_timeout_seconds": 12, "allow_duplicate": True},
        )
        instance_id = launched["instance_id"]
        if detail.get("demo_run_path"):
            result = request("/api/instances/" + instance_id + "/demo-run", {})
            assert result["success"]
            body = result["response_json"]
            metrics = result.get("metrics") or {}
            # Verify the same-origin application UI, not the process loopback URL.
            request("/apps/" + instance_id + "/")
        else:
            for _ in range(120):
                result = request("/api/instances/" + instance_id)
                if result["status"] in ("completed", "failed"):
                    break
                time.sleep(0.5)
            assert result["status"] == "completed", result["status"]
            metrics = result.get("metrics") or {}
            body = json.loads(Path("/data/verification/cleaner-output.json").read_text())
        stages = metrics.get("stages", [])
        assert (
            stages
            and sum(x.get("output_items", 0) for x in stages if x.get("op_type") == "source") > 0
        )
        assert not sum(x.get("errors", 0) for x in stages)
        if ident == "ticket_triage_api":
            assert body["processed_event_count"] == 10 and body["high_priority_count"] == 5
        elif ident == "supply_chain_alert_api":
            assert (
                body["processed_event_count"] == 10 and body["dashboard"]["open_alert_count"] == 8
            )
        else:
            rows = body if isinstance(body, list) else body.get("records", body.get("data", []))
            assert len(rows) == 3 and sum(bool(x["is_duplicate"]) for x in rows) == 1
        request("/api/instances/" + instance_id + "/logs")
        report["applications"][ident] = {
            "passed": True,
            "instance_id": instance_id,
            "stage_count": len(stages),
            "operator_errors": 0,
            "response": body,
        }
        print(json.dumps({"app": ident, "passed": True, "stage_count": len(stages)}), flush=True)
    path = Path("/data/verification")
    path.mkdir(exist_ok=True)
    (path / "demo.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"catalog": len(apps), "passed": True}), flush=True)


if __name__ == "__main__":
    main()
