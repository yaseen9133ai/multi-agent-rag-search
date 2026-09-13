# Grokking the telemetry stack: OpenTelemetry, OTLP & OpenInference

A short, plain-English reference for the observability vocabulary used in this project. Read it once
and the trace you see in Langfuse (and the env vars in the deploy code) stop being magic.

## The one-sentence mental model

> Your agent emits **spans**; **OpenInference** creates them; **OTLP** ships them; a **backend**
> displays them as a **trace**.

That's the whole pipeline. Everything below is just naming the pieces.

## Why this exists (the problem it solves)

You want to *see* what your agent does — which sub-agent fired, what the retrieval returned, how long
each step took. Naively, every monitoring tool has its own SDK and data format, so wiring up a new
tool means re-instrumenting your app. **OpenTelemetry** fixes that: instrument **once**, send
**anywhere**. Producing telemetry is decoupled from consuming it.

## The five pieces

1. **OpenTelemetry (OTel)** — the vendor-neutral open standard for telemetry: **traces, metrics,
   logs**. It's a **CNCF project** — the *Cloud Native Computing Foundation*, the vendor-neutral
   open-source foundation (part of the Linux Foundation) that also stewards Kubernetes and Prometheus.
   "CNCF project" is shorthand for *"governed in the open by a neutral foundation, not owned by one
   vendor"* — which is exactly why essentially every observability backend speaks it. *ADK is built on
   it*, which is what makes all of this portable.

2. **Span & trace** — the data model.
   - A **span** is one unit of work with a start time, end time, and attributes: *one agent step,
     one model call, one tool call*.
   - A **trace** is all the spans for a single request, arranged as a **tree**. The thing you read in
     Langfuse (or Phoenix, locally) *is* a trace: `supervisor → model call → search tool → answer`.

3. **OTLP (OpenTelemetry Protocol)** — *how* spans travel over the wire to a backend. Think of it as
   a standard shipping container every backend knows how to unload. When you set
   `OTEL_EXPORTER_OTLP_ENDPOINT`, that URL is where the containers are sent.
   *(It's **OTLP**, not "OLTP" — the latter is a database term. Easy to mistype.)*

4. **Exporter, backend, collector** — the delivery.
   - An **exporter** packages spans as OTLP and sends them.
   - A **backend** stores and visualizes them: **Phoenix**, **Langfuse**, **Google Cloud Trace**, …
   - A **Collector** (optional) sits in the middle — *receive → process → fan out* — so you can send
     the same traces to several backends at once without changing the app.

5. **OpenInference** — the missing link for *agents*. Plain OTel is the plumbing, but something has
   to actually *create* well-shaped spans for an LLM agent. OpenInference is a set of conventions +
   **auto-instrumentation** for LLM/agent frameworks. Its ADK instrumentor wraps the `Runner`,
   agents, model calls, and tools, so every run becomes a labelled span. It works at the **framework**
   level — which is why it captures our **Cohere-via-LiteLLM** agent exactly as it would Gemini —
   and it complements OTel's **GenAI semantic conventions** (the agreed attribute names for things
   like token counts).

## How it shows up in this repo

- `agent/src/observability.py` calls the OpenInference instrumentor — **no
  change to agent logic** — and picks the backend from what you've configured: if
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set it exports raw **OTLP** (local dev → **Phoenix**); if the
  `LANGFUSE_*` keys are set it uses the **Langfuse SDK** (production). We use the SDK in prod because
  Langfuse renders **SDK-ingested** traces in seconds, while the raw-OTLP path can take minutes to show
  up in its UI. Same instrumentation either way; the backend is chosen by config, not code.

## Generic APM vs. LLM-native backends

All of these read the *same* OTel/OpenInference spans — they differ in what they show:

- **Generic APM** — **Google Cloud Trace**, Jaeger, SigNoz. You get the **span waterfall**, **latency**,
  and span **attributes**. Great for "where did the time go / what called what." Cloud Trace is the
  zero-extra-account option when your agent already runs on GCP (`adk web --otel_to_cloud`, or an OTel
  Collector that fans out to it). Explore in **Cloud Console → Trace → Trace Explorer**.
- **LLM-native** — **Langfuse**, **Phoenix (Arize)**. The same traces *plus* the things that matter
  for agents: **prompt/response inspection**, **token & cost** tracking, **dataset capture**, and
  **LLM-as-judge online evals**. That's why this project uses Phoenix (local) / Langfuse (prod),
  even though Cloud Trace would also "have the data."

Same telemetry underneath; pick the viewer for the job — or fan out to several at once via a Collector.

## Two practical notes

- **Trace structure always, payloads selectively.** Capturing full prompt/response *content* in
  spans is great for debugging but a privacy/cost decision in production. Redact it while keeping the
  trace shape with `OPENINFERENCE_HIDE_INPUTS=true` / `OPENINFERENCE_HIDE_OUTPUTS=true` (or, on ADK's
  native Cloud path, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY`).
- **Cosmetic caveat:** OpenInference currently labels the provider span attribute `google` even for
  non-Gemini models. Traces are complete and correct; only that one label is off.

## Glossary (one-liners)

| Term | Meaning |
|---|---|
| **OpenTelemetry / OTel** | Open standard for traces, metrics, logs |
| **CNCF** | Cloud Native Computing Foundation — vendor-neutral open-source foundation (part of the Linux Foundation) that hosts OpenTelemetry, Kubernetes, Prometheus, … |
| **Span** | One operation (agent step / model call / tool call) |
| **Trace** | The tree of spans for one request |
| **OTLP** | The wire protocol that ships telemetry to a backend |
| **Exporter** | Packages + sends spans over OTLP |
| **Backend** | Stores + visualizes traces (Phoenix, Langfuse, Cloud Trace) |
| **Collector** | Optional middleman that fans telemetry out to many backends |
| **OpenInference** | Auto-instrumentation + conventions that turn ADK runs into spans |
| **Semantic conventions** | Agreed attribute names (e.g. token counts) so tools interoperate |
| **Instrumentation** | The code that emits spans (here, applied to ADK with one line) |

## References

- OpenTelemetry — concepts & docs: <https://opentelemetry.io/docs/>
- OTLP specification: <https://opentelemetry.io/docs/specs/otlp/>
- OTel GenAI semantic conventions: <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
- OTel Collector: <https://opentelemetry.io/docs/collector/>
- ADK observability (logging / metrics / traces): <https://adk.dev/observability/> · traces: <https://adk.dev/observability/traces/>
- ADK observability integrations (Phoenix, Langfuse, Cloud Trace, …): <https://adk.dev/integrations/?topic=observability>
- OpenInference — Google ADK instrumentation: <https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-google-adk> · `pip install openinference-instrumentation-google-adk`
- Arize Phoenix — Google ADK tracing: <https://arize.com/docs/phoenix/integrations/llm-providers/google-gen-ai/google-adk-tracing>
- Langfuse — Google ADK integration: <https://langfuse.com/integrations/frameworks/google-adk>
