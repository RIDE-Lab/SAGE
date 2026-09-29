# SAGE - Streaming-Augmented Generative Execution

> A dataflow-native framework for modular, controllable, and transparent LLM-augmented reasoning.

[![Build & Test](https://github.com/RIDE-Lab/SAGE/actions/workflows/ci-build-test.yml/badge.svg?branch=main)](https://github.com/RIDE-Lab/SAGE/actions/workflows/ci-build-test.yml)
[![codecov](https://codecov.io/gh/RIDE-Lab/SAGE/branch/main/graph/badge.svg)](https://codecov.io/gh/RIDE-Lab/SAGE)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![PyPI version](https://badge.fury.io/py/isage.svg)](https://badge.fury.io/py/isage)
[![GitHub Issues](https://img.shields.io/github/issues/RIDE-Lab/SAGE)](https://github.com/RIDE-Lab/SAGE/issues)
[![GitHub Stars](https://img.shields.io/github/stars/RIDE-Lab/SAGE?style=social)](https://github.com/RIDE-Lab/SAGE/stargazers)

**SAGE** turns LLM reasoning workflows into explicit dataflow pipelines. It is designed for
streaming execution, transparent orchestration, controllable serving, and modular integration with
retrieval, memory, tool-use, and inference-engine backends.

SAGE is the shared flagship product and technical vision of the IntelliStream research ecosystem.
Its name captures that vision: **Streaming-Augmented Generative Execution** applies
streaming-computing principles—explicit dataflow, state, backpressure, and continuous execution—to
LLM inference and agent systems. RIDE Lab is the principal engineering steward of the core
repositories, while DataSys and vLLM-HUST retain ownership of their data-system and inference
boundaries. See [GOVERNANCE.md](./GOVERNANCE.md) for the stewardship contract.

## News

- **May 2026**: The SAGE paper was accepted to **ICML 2026**: *SAGE: A Dataflow-Native Framework for
  Modular, Controllable, and Transparent LLM-Augmented Reasoning*.
  [OpenReview](https://openreview.net/forum?id=TXcFJdT7at) |
  [author PDF](https://shuhao.sage.org.ai/contents/research_papers/2026/2026_sage_icml_2026.pdf).

## ICPP 2026 Demo

The CPU demo delivery includes Docker startup scripts, model-provider adapters, validation tools,
and the printable poster. See the [deployment guide](tools/icpp-demo/README.md) and
[poster](tools/icpp-demo/docs/poster/SAGE-ICPP-Demo-Poster.pdf).

```sh
git clone https://github.com/intellistream/SAGE.git
cd SAGE/tools/icpp-demo
sh scripts/start-hub.sh
```

The image includes application dependencies but not model weights, datasets, or credentials.

## Current Scope

The main repository now provides the installable SAGE core:

- **Dataflow API**: `DataStream` and declarative stream pipeline composition
- **Runtime**: `LocalEnvironment`, `FlowNetEnvironment`, `JobManager`, scheduling, and execution
  services
- **Serving integration**: OpenAI-compatible gateway contracts and external inference-engine hooks
- **Foundation**: centralized ports, XDG user paths, config, logging, and model registry utilities
- **CLI**: compact `sage` commands for verification, status, runtime inspection, serving, chat, and
  lightweight indexing

Optional RAG, memory, intent, tool-use, data, and inference-engine capabilities remain explicit
extras or independent packages rather than implicit default dependencies.

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
git clone https://github.com/RIDE-Lab/SAGE.git && cd SAGE
git checkout main
./quickstart.sh --dev --yes

# Verify the in-tree core surface
sage verify

# Explore tutorials from the separate learning repo
git clone https://github.com/RIDE-Lab/sage-tutorials.git
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

The public package surface follows a small layered core:

```text
L3 Interface : sage.cli + sage.serving
L2 Runtime   : sage.runtime + sage.stream
L1 Base      : sage.foundation
Optional     : sage.edge + external adapters and engines
```

Default installs focus on `sage.foundation`, `sage.stream`, `sage.runtime`, `sage.serving`, and
`sage.cli`. Distributed execution is exposed through `FlowNetEnvironment`; local development and
single-process deployments use `LocalEnvironment`.

```text
Local mode       : LocalEnvironment + in-process operator replicas
Distributed mode : FlowNetEnvironment + FlowNet actor replicas
Serving mode     : sage.serving + OpenAI-compatible gateway contracts
Adapter mode     : explicit extras for RAG, memory, intent, tool-use, and data packages
```

See [SAGE Ecosystem](#sage-ecosystem) for related repositories and optional packages.

📖 **[Architecture Guide](https://sage.org.ai/architecture/)** - Canonical ownership boundaries and
dependency rules for the meta repo

📌 **[Layer Ownership Matrix v1 (Wave A)](https://sage.org.ai/architecture/layer-ownership/)** -
Canonical L1-L4 workspace ownership, independent sub-repo coordination boundary (including `sagellm`
capabilities), forbidden directions, and boundary refactor review checklist

## Installation

### Quickstart (Recommended)

```bash
git clone https://github.com/RIDE-Lab/SAGE.git && cd SAGE
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
git clone https://github.com/RIDE-Lab/sage-tutorials.git
cd sage-tutorials
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
- **Tutorials**: [RIDE-Lab/sage-tutorials](https://github.com/RIDE-Lab/sage-tutorials)

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

## Citation

If SAGE is useful in your research, please cite the ICML 2026 paper:

Paper record: https://openreview.net/forum?id=TXcFJdT7at

```bibtex
@inproceedings{liu2026sage,
  title = {SAGE: A Dataflow-Native Framework for Modular, Controllable, and Transparent LLM-Augmented Reasoning},
  author = {Liu, Jun and Liu, Peilin and Zhang, Ruicheng and Zhang, Senlei and Chen, Yanbo and Wang, Ziao and Yang, Jinyun and Wang, Mingqi and Zhang, Shuhao and Liao, Xiaofei and Jin, Hai},
  booktitle = {International Conference on Machine Learning (ICML)},
  year = {2026}
}
```

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

Older split repositories and thin wrappers are maintained only when they own a clear external
capability boundary or migration purpose.

## Community

Use [GitHub Issues](https://github.com/RIDE-Lab/SAGE/issues) for bugs and feature requests. For
broader design discussions, use [GitHub Discussions](https://github.com/RIDE-Lab/SAGE/discussions)
when enabled for the repository.

Repository placement under `RIDE-Lab` identifies the core engineering steward; it does not make SAGE
an exclusive product of one research organization. Cross-organization contributions follow the
ownership boundaries documented in [GOVERNANCE.md](./GOVERNANCE.md).

## License

SAGE is licensed under the [MIT License](./LICENSE).
