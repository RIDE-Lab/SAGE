# Authenticated external model test

The CPU image stays unchanged. This branch supplies an opt-in, read-only mounted provider adapter.
It translates Cloudflare embedding and reranker results to the application's OpenAI/Jina-style
structures. Every inference request is sent to the real provider; it contains no model responses or
weights. The adapter listens only on container loopback port 19080.

Run the startup script with `--init-env` first, then edit the private `.env`:

```dotenv
SAGE_ENTRYPOINT=/opt/demo-tools/start-providers.sh
SAGE_LLM_BASE_URL=https://nowcoding.ai/v1
SAGE_LLM_MODEL=gpt-5.5
SAGE_OPENAI_API_KEY=YOUR_LLM_KEY
SAGE_EMBEDDING_PROVIDER=cloudflare
SAGE_EMBEDDING_BASE_URL=https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai/run/@cf/baai/bge-m3
SAGE_EMBEDDING_MODEL=@cf/baai/bge-m3
SAGE_EMBEDDING_API_KEY=YOUR_CLOUDFLARE_TOKEN
SAGE_RERANKER_PROVIDER=cloudflare
SAGE_RERANKER_BASE_URL=https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai/run/@cf/baai/bge-reranker-base
SAGE_RERANKER_MODEL=@cf/baai/bge-reranker-base
SAGE_RERANKER_API_KEY=YOUR_CLOUDFLARE_TOKEN
SAGE_MODEL_TIMEOUT=90
LOCAL_DEMO_PORT=18400
```

Run `sh scripts/start-hub.sh`, or use `sh scripts/start-local.sh` with the separately supplied image
archive in `artifacts/`. To isolate a second deployment, set the shell environment
`SAGE_COMPOSE_PROJECT=sage-model-check` before running the script. The PowerShell scripts also
accept this environment variable. Docker receives the keys as environment variables; do not commit
`.env` or print `docker compose config` output containing them.

The application process inherits loopback model endpoints. The upstream URLs and provider keys
remain in Docker configuration. `/api/model-services` shows configured status until a real request
succeeds or fails, then reports the last verified time; refreshing the page does not make paid model
requests.

For the default Hub project, run:

```sh
docker compose -p sage-icpp-demo-hub -f compose.hub.yaml exec -T demo python /opt/demo-tools/smoke_demo.py
docker compose -p sage-icpp-demo-hub -f compose.hub.yaml exec -T demo python /opt/demo-tools/verify_models.py
```

For an offline project substitute its project name and `compose.local.yaml`. The model test incurs
provider usage. It checks nonzero embeddings, reranker ordering, a real LLM response and the
existing Student Improvement application client. Reports and generated guidance go into
`/data/verification` and `/data/model-student-guidance.txt`, without keys. Deterministic Ticket
Triage, Supply Chain Alert and Data Cleaner checks do not assert that their existing business
pipelines use embedding or reranking. The adapter enables clients using the configured endpoints;
applications with unrelated hard-coded backends still need their own configuration.

The 90-second upstream limit does not change timeouts hard-coded in individual application clients.
Errors remain errors, never substituted responses. Restarting the container resets model status to
configured until the next real request. The read-only `scripts/` mount is required when this adapter
is enabled.
