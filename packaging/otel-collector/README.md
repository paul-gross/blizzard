# Example trace collector

`collector.yaml` is an example OpenTelemetry Collector configuration that receives OTLP over HTTP and fans every trace
to two backends: a self-hosted store (Tempo-shaped, OTLP over gRPC) and a hosted service (Honeycomb-shaped, OTLP over
HTTP). Endpoints and credentials come from the environment, never from the file.

It targets the `otelcol-contrib` distribution. `mise run collector-config-check` installs the pinned release for that
task alone and runs `otelcol-contrib validate` over the file, with placeholder values for the environment variables it
reads. Local-only: the binary is large and the CI gate is network-free.

[`docs/deployment/tracing.md`](../../docs/deployment/tracing.md) explains each block.
