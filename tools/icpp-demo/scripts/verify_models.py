"""Opt-in real model requests from the running container; no secrets in output."""

import concurrent.futures
import json
import math
import os
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:19080"
DOCS = ["Customer cannot sign in to their account.", "The shipment arrives tomorrow."]


def request(path, payload):
    started = time.monotonic()
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.load(response)
    return data, round(time.monotonic() - started, 3)


def check_embedding():
    data, elapsed = request(
        "/embedding/v1/embeddings", {"model": os.environ["SAGE_EMBEDDING_MODEL"], "input": DOCS}
    )
    vectors = [x["embedding"] for x in data["data"]]
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
    assert all(
        all(math.isfinite(v) for v in vector) and any(v != 0 for v in vector) for vector in vectors
    )
    return {
        "service": "embedding",
        "passed": True,
        "vectors": len(vectors),
        "dimensions": len(vectors[0]),
        "seconds": elapsed,
    }


def check_reranker():
    data, elapsed = request(
        "/reranker/v1/rerank",
        {
            "model": os.environ["SAGE_RERANKER_MODEL"],
            "query": "customer login failure",
            "documents": DOCS,
            "top_n": 2,
        },
    )
    results = data["results"]
    assert len(results) == 2 and results[0]["index"] == 0
    assert results[0]["relevance_score"] > results[1]["relevance_score"]
    return {"service": "reranker", "passed": True, "results": results, "seconds": elapsed}


def check_llm():
    data, elapsed = request(
        "/llm/v1/chat/completions",
        {
            "model": os.environ["SAGE_LLM_MODEL"],
            "messages": [{"role": "user", "content": "Reply with exactly: SAGE_OK"}],
            "max_tokens": 32,
        },
    )
    content = data["choices"][0]["message"]["content"]
    assert content.strip() == "SAGE_OK", content
    return {
        "service": "llm",
        "passed": True,
        "model": data.get("model"),
        "response": content,
        "seconds": elapsed,
    }


def check_real_application():
    from sage.apps.student_improvement.llm import SageOpenAIClient, SageOpenAISettings
    from sage.apps.student_improvement.service import (
        _to_payload,
        create_demo_application_service,
        load_demo_exam_records,
    )

    service = create_demo_application_service(storage_path="/data/model-student-check.json")
    exams = load_demo_exam_records()
    for exam in exams:
        service.import_exam(exam)
    sid = exams[0].student_id
    client = SageOpenAIClient(
        SageOpenAISettings(
            base_url=BASE + "/llm/v1",
            model=os.environ["SAGE_LLM_MODEL"],
            api_key=os.environ["SAGE_OPENAI_API_KEY"],
        )
    )
    started = time.monotonic()
    guidance = client.generate_learning_guidance(
        student_profile=_to_payload(service.get_student_profile(sid)),
        diagnosis=_to_payload(service.get_student_diagnosis(sid)),
        wrong_question_bank=_to_payload(service.get_wrong_question_bank(sid)),
        knowledge_graph=_to_payload(service.get_student_knowledge_graph(sid)),
    )
    assert len(guidance) > 20
    Path("/data/model-student-guidance.txt").write_text(guidance)
    return {
        "service": "student_improvement real application client",
        "passed": True,
        "guidance_characters": len(guidance),
        "seconds": round(time.monotonic() - started, 3),
    }


def main():
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(lambda fn: fn(), [check_embedding, check_reranker, check_llm]):
            results.append(result)
            print(json.dumps(result), flush=True)
    results.append(check_real_application())
    report = Path("/data/verification")
    report.mkdir(exist_ok=True)
    (report / "models.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results[-1]), flush=True)


if __name__ == "__main__":
    main()
