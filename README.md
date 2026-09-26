# SAGE - Streaming-Augmented Generative Execution

> A declarative, composable framework for building transparent LLM-powered systems through dataflow
> abstractions.

## ICPP 2026 Demo — Docker and Poster

This branch provides the SAGE ICPP Demo delivery.
[Docker startup and configuration](tools/icpp-demo/README.md) ·
[Printable poster](tools/icpp-demo/docs/poster/SAGE-ICPP-Demo-Poster.pdf) ·
[Editable SVG](tools/icpp-demo/docs/poster/SAGE-ICPP-Demo-Poster.svg).

```sh
git clone --branch icpp-demo --single-branch https://github.com/RIDE-Lab/SAGE.git
cd SAGE/tools/icpp-demo
sh scripts/start-hub.sh
```

Windows with Linux containers: run `scripts/start-hub.ps1` from the same directory. Open
http://localhost:18400/ui/ after startup. The scripts create `.env` if absent. Image:
`liujun4hust/sage-icpp-demo:20260926-cpu-full` (Linux AMD64). CPU dependencies are included; model
weights, datasets and credentials remain separately configurable. See the deployment README for
tested scope and limitations.

## 🚀 Quick Start

______________________________________________________________________

[![Build & Test](https://github.com/intellistream/SAGE/actions/workflows/ci-build-test.yml/badge.svg?branch=main)](https://github.com/intellistream/SAGE/actions/workflows/ci-build-test.yml)
[![codecov](https://codecov.io/gh/intellistream/SAGE/branch/main/graph/badge.svg)](https://codecov.io/gh/intellistream/SAGE)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![PyPI version](https://badge.fury.io/py/isage.svg)](https://badge.fury.io/py/isage)
[![GitHub Issues](https://img.shields.io/github/issues/intellistream/SAGE)](https://github.com/intellistream/SAGE/issues)
[![GitHub Stars](https://img.shields.io/github/stars/intellistream/SAGE?style=social)](https://github.com/intellistream/SAGE/stargazers)

**SAGE** is a high-performance streaming framework for building AI-powered data processing
pipelines. Transform complex LLM reasoning workflows into transparent, scalable, and maintainable
systems through declarative dataflow abstractions.

## 2026 Focus Reset

SAGE is being refocused into a **stream-first inference service system** instead of a broad
collection of loosely coupled apps.

- **Keep the stream core**: `DataStream` + declarative pipeline composition remain the product
  identity
- **Keep the execution core**: `LocalEnvironment`, `JobManager`, scheduling, service runtime
- **Keep the serving integration plane**: OpenAI-compatible gateway access, model lifecycle entry,
  and control-plane integration contracts
- **Keep distributed execution optional**: `FlowNetEnvironment` remains the FlowNet-first optional
  distributed runtime entry, instead of falling back to new Ray-based paths
- **Keep the operating substrate**: centralized ports, XDG user paths, model registry, logs,
  health/status surfaces
- **De-emphasize apps**: UI-first repos are no longer the product center; optional apps should sit
  outside the core or be retired

This direction also makes SAGE easier to position as a **SAGE Zoo member**: a reusable
stream-oriented runtime + serving component that other systems can call through stable APIs instead
of embedding internal implementation details.

Important boundary: `isagellm` remains an **independent inference engine**. SAGE integrates with it
as an external engine/service capability; SAGE should not absorb `isagellm` internals into the main
repository.

Preferred in-tree surface during consolidation:

- `from sage.stream import DataStream`
- `from sage.runtime import LocalEnvironment, FlowNetEnvironment, JobManager`
- `from sage.foundation import SagePorts, SageUserPaths`
- `from sage.serving import SageServeConfig, build_sagellm_gateway_command, probe_gateway`

## Key Features

- **Stream-First**: Dataflow is the primary abstraction, not an afterthought
- **Production-Ready**: Local-first execution, optional distributed processing, fault tolerance,
  comprehensive monitoring
- **Developer Experience**: Complex AI pipelines in just a few lines of code
- **High Performance**: Optimized streaming with intelligent memory management
- **Observable**: Built-in visibility into execution and performance
- **Flexible**: CPU-only or GPU nodes with intelligent resource scheduling

## Quick Start

The recommended main-repo surface is the in-tree core:

- `from sage.foundation import BatchFunction, MapFunction, SinkFunction, SagePorts, SageUserPaths`
- `from sage.stream import DataStream`
- `from sage.runtime import LocalEnvironment, FlowNetEnvironment, JobManager`
- `from sage.serving import SageServeConfig, build_sagellm_gateway_command, probe_gateway`

RAG, memory, intent, and tool-use capabilities are optional adapters. They should be installed
explicitly instead of being treated as the default root-package contract.

### Runtime parallelism semantics

- `LocalEnvironment` now honors transformation `parallelism` for non-source operators.
- In local mode, SAGE creates in-process worker replicas for eligible transformations and exposes
  `ctx.parallel_index` plus `ctx.parallelism` on each replica.
- Keyed streams keep stable key-to-replica routing locally; unkeyed streams use round-robin routing
  across local replicas.
- Source transformations are still polled locally in-process; `parallelism` applies to downstream
  transformation replicas rather than spawning multiple independent local source processes.
- `FlowNetEnvironment` compiles the same `parallelism` hints into FlowNet actor replica counts, so
  local and FlowNet execution share the same user-facing operator parallelism contract.

### Minimal local pipeline

```python
from sage.foundation import BatchFunction, MapFunction, SinkFunction
from sage.runtime import LocalEnvironment, StopSignal


class NumberBatch(BatchFunction):
  def __init__(self) -> None:
    super().__init__()
    self._current = 0

  def execute(self):
    if self._current >= 4:
      return StopSignal("numbers-done")
    value = self._current
    self._current += 1
    return value


class DoubleValue(MapFunction):
  def execute(self, data: int) -> int:
    return data * 2


class PrintSink(SinkFunction):
  def execute(self, data: int) -> None:
    print(data)


env = LocalEnvironment("hello-sage")
env.from_batch(NumberBatch).map(DoubleValue).sink(PrintSink)
env.submit(autostop=True)
```

### Try it yourself

```bash
git clone https://github.com/intellistream/SAGE.git && cd SAGE
git checkout main
./quickstart.sh --dev --yes

# Verify the in-tree core surface
sage verify

# Explore tutorials from the separate learning repo
git clone https://github.com/intellistream/sage-tutorials.git
python sage-tutorials/L1-common/hello_world.py
```

### Current CLI quick reference

- `sage version`
- `sage status`
- `sage doctor`
- `sage verify`
- `sage runtime nodes`
- `sage serve gateway --json`
- `sage serve gateway --probe --json`
- `sage chat --ask "Hello, SAGE!"`
- `sage index ingest --source ./docs --index local-docs`

### Core verification

```bash
sage verify
sage status
sage runtime nodes
sage serve gateway --json
sage chat --ask "Hello, SAGE!"
```

## Architecture

Current baseline is a **4-tier workspace architecture (L1-L4)**:

```text
L4: application repos (optional)      # App / UI / benchmark
L3: sage.cli                          # CLI entrypoint        (in-tree)
L2: sage.runtime + sage.stream        # Runtime / scheduler   (in-tree)
L1: sage.foundation                   # Foundation            (in-tree)
```

Notes:

- Historical split packages may still exist as transitional published compatibility channels.
- The main repository now owns the preferred product surface directly: `sage.foundation` +
  `sage.stream` + `sage.runtime` + `sage.serving` + `sage.cli`.
- Historical split foundation/runtime/CLI repos are therefore no longer the desired long-term
  product boundary, even when some transitional imports still exist outside the main install
  contract.

Target product convergence is narrower than the historical workspace shape:

```text
SAGE Inference Service System
L3 Interface   : CLI + OpenAI-compatible service entry + external integration surface
L2 Runtime     : LocalEnvironment + DataStream + JobManager + scheduler + execution services
Optional Dist. : FlowNetEnvironment (FlowNet-backed distributed execution)
L1 Foundation  : config + ports + user paths + model registry + logging
Optional       : RAG / memory / tool-use / benchmark adapters
```

In other words, SAGE is moving toward a smaller, sharper center: **stream + runtime + serving +
operations**, with distributed execution available as an optional scale-out mode.

Repo-retirement gate: do not retire historical split repos solely based on packaging cleanup. The
main repo has now removed direct historical runtime split-package dependency pins and owns its local
runtime path in-tree, but ecosystem compatibility imports, external repos, and remaining
transitional release channels still need deliberate follow-up before those repos can be fully
retired.

See [SAGE Ecosystem](#sage-ecosystem) for all independent sub-repositories with CI status, PyPI
packages, and categorized listings.

📖 **[Architecture Guide](https://sage.org.ai/architecture/)** - Canonical ownership boundaries and
dependency rules for the meta repo

📌 **[Layer Ownership Matrix v1 (Wave A)](https://sage.org.ai/architecture/layer-ownership/)** -
Canonical L1-L4 workspace ownership, independent sub-repo coordination boundary (including `sagellm`
capabilities), forbidden directions, and boundary refactor review checklist

## Installation

### Quickstart (Recommended)

```bash
git clone https://github.com/intellistream/SAGE.git && cd SAGE
./quickstart.sh --dev --yes
# or
./quickstart.sh --standard --yes
```

Install mode semantics:

- `--standard`: install the root `isage` package and resolve external dependencies from PyPI.
- `--dev`: install the same root package plus development tooling, then switch sibling SAGE repos to
  editable installs when they are present in the workspace.

### PyPI install

```bash
python -m pip install isage
python -m pip install 'isage[dev]'
```

### What the default install includes

- `sage.foundation`
- `sage.stream`
- `sage.runtime`
- `sage.serving`
- `sage.cli`

The default install does not auto-install `isagellm`. SAGE keeps the inference engine boundary
explicit and integrates with it through `sage.serving` and CLI entrypoints.

### Optional integrations and extras

```bash
python -m pip install 'isage[sagellm]'
python -m pip install 'isage[serving-edge]'
python -m pip install 'isage[capability-adapters]'
python -m pip install 'isage[capability-tooluse]'
python -m pip install 'isage[full]'
```

Current extras map to these owned or external capabilities:

- `sagellm`: external inference engine integration (`isagellm`)
- `serving-edge`: in-tree edge shell runtime for `sage-edge`
- `capability-adapters`: optional adapters for intent, RAG, and memory (`isage-libs-intent`,
  `isage-rag`, `isage-neuromem`)
- `capability-tooluse`: optional SIAS tool-use adapter (`isage-sias`)
- `full`: all supported extras above plus the optional data package (`isage-data`)

On Python 3.13+, `isagellm` integration may still be unavailable until upstream wheels are
published. Core SAGE stream/runtime development remains independent of that engine.

### Verification & troubleshooting

```bash
sage doctor
sage verify
sage chat --help
sage index ingest --help
./quickstart.sh --doctor
```

### CLI Command Reference

The current main-repo `sage` command surface is intentionally small and grouped around the core
product boundary:

| Command                                                | Purpose                                                                               |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| `sage version`                                         | Print installed SAGE version                                                          |
| `sage status`                                          | Show local config/data/state paths and gateway summary                                |
| `sage doctor`                                          | Run lightweight environment diagnostics                                               |
| `sage verify`                                          | Smoke-check the in-tree core surface                                                  |
| `sage runtime nodes`                                   | List runtime-visible nodes                                                            |
| `sage serve gateway --json`                            | Print the external `sagellm` gateway launch contract                                  |
| `sage serve gateway --probe --json`                    | Probe the configured gateway health endpoint                                          |
| `sage chat`                                            | Start chat via `sagellm` gateway, direct CLI, or configured OpenAI-compatible backend |
| `sage chat --ask "..."`                                | Run one-shot chat                                                                     |
| `sage index ingest --source ./docs --index local-docs` | Record lightweight local index metadata                                               |

```bash
sage verify
sage runtime nodes
sage serve gateway --json
sage chat --ask "Hello, SAGE!"
sage index ingest --source ./docs --index local-docs
```

📖 **Detailed guides**: [Installation Guide](https://sage.org.ai/guides/installation/) |
[Troubleshooting](https://sage.org.ai/guides/troubleshooting/) |
[Validation](https://sage.org.ai/guides/validation/) |
[Optimization Tips](tools/install/docs/INSTALLATION_OPTIMIZATION.md)

⚠️ **Known Issues**: If you encounter transformers version conflicts when installing multiple SAGE
packages, prefer checking [DEVELOPER.md](./DEVELOPER.md) and the package-specific READMEs first.

## Environment Configuration

```bash
cp .env.template .env    # Copy template
# Edit .env and add your API keys (OPENAI_API_KEY, HF_TOKEN, etc.)
```

📖 **API key setup**: See [.env.template](./.env.template) for all available options

## 📚 Tutorials

Tutorials live in the separate `sage-tutorials` repository so that learning materials can evolve
without drifting the main package contract.

```bash
git clone https://github.com/intellistream/sage-tutorials.git
python L1-common/hello_world.py
```

Start with `sage-tutorials/QUICK_START.md` and `sage-tutorials/L1-common/hello_world.py`. Optional
adapter and historical migration topics remain documented in that repository, not in the main repo
README.

## Documentation & Resources

- **Documentation**: [https://sage.org.ai/](https://sage.org.ai/)
- **Architecture**: [https://sage.org.ai/architecture/](https://sage.org.ai/architecture/)
- **Developer guide**: [DEVELOPER.md](./DEVELOPER.md)
- **Contribution guide**: [CONTRIBUTING.md](./CONTRIBUTING.md)
- **Tutorials**: [intellistream/sage-tutorials](https://github.com/intellistream/sage-tutorials)

## Contributing

We welcome contributions! See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

```bash
git checkout -b feature/my-feature
./quickstart.sh --dev --yes
# Make changes, add tests
sage-dev quality check --all-files --readme
sage-dev project test --coverage
git commit -m "feat(runtime): describe your change"
git push -u origin feature/my-feature
```

Primary branch policy is `main`. For larger efforts, work from a `feature/*` branch and merge back
through a pull request.

## Team Information

**🔒 Team assignments and sensitive information** are maintained in a private repository to protect
member privacy.

- **Public**: Project-level information is available in this repository
- **Private**: Team member assignments, funding details, and contact information are accessible to
  authorized members only
- **Access**: Contact project management for access to the private repository

## Developer Tools

```bash
sage-dev quality fix --all-files
sage-dev quality check --all-files --readme
sage-dev project test --coverage
./quickstart.sh --doctor
```

📖 **Complete reference**: [DEVELOPER.md](./DEVELOPER.md)

## SAGE Ecosystem

SAGE keeps the active ecosystem list narrow and aligned with the current package contract.

### In-tree core

- `sage.foundation`
- `sage.stream`
- `sage.runtime`
- `sage.serving`
- `sage.edge`
- `sage.cli`

### External engine and service boundary

- `isagellm`: independent inference engine used through `sage.serving` and `sage chat`
- `sagellm-gateway`: external gateway service boundary for OpenAI-compatible access

### Optional adapters

- `isage-rag`: retrieval adapters and RAG-specific components
- `isage-neuromem`: memory and persistence adapters
- `isage-libs-intent`: intent classification adapter
- `isage-sias`: tool-use and continual-learning adapter
- `isage-data`: optional dataset package included by `isage[full]`

### Related repositories

- `sage-tutorials`: learning materials and runnable examples
- `sage-studio`: application/UI layer above the core CLI and runtime
- `sage-docs`: user-facing documentation

Historical split repos or thin wrappers should be treated as migration or retirement targets unless
they own a clear external capability boundary.

## Community

**💬 [Join SAGE Community](./docs/COMMUNITY.md)** - WeChat, QQ, Slack, GitHub Discussions

## License

SAGE is licensed under the [MIT License](./LICENSE).
