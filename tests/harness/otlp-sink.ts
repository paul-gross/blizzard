import * as http from 'node:http';
import * as zlib from 'node:zlib';
import * as protobuf from 'protobufjs';

const TRACES_PATH = '/v1/traces';
const EXPORT_PATHS = [TRACES_PATH, '/v1/metrics', '/v1/logs'];

/** The subset of `opentelemetry-proto` a span's decode needs, with upstream field numbers. */
const schema = protobuf.Root.fromJSON({
  nested: {
    AnyValue: {
      fields: {
        stringValue: { type: 'string', id: 1 },
        boolValue: { type: 'bool', id: 2 },
        intValue: { type: 'int64', id: 3 },
        doubleValue: { type: 'double', id: 4 },
        arrayValue: { type: 'ArrayValue', id: 5 },
        kvlistValue: { type: 'KeyValueList', id: 6 },
        bytesValue: { type: 'bytes', id: 7 },
      },
    },
    ArrayValue: { fields: { values: { rule: 'repeated', type: 'AnyValue', id: 1 } } },
    KeyValueList: { fields: { values: { rule: 'repeated', type: 'KeyValue', id: 1 } } },
    KeyValue: { fields: { key: { type: 'string', id: 1 }, value: { type: 'AnyValue', id: 2 } } },
    Resource: { fields: { attributes: { rule: 'repeated', type: 'KeyValue', id: 1 } } },
    InstrumentationScope: {
      fields: { name: { type: 'string', id: 1 }, version: { type: 'string', id: 2 }, attributes: { rule: 'repeated', type: 'KeyValue', id: 3 } },
    },
    Span: { fields: { name: { type: 'string', id: 5 }, attributes: { rule: 'repeated', type: 'KeyValue', id: 9 } } },
    ScopeSpans: { fields: { scope: { type: 'InstrumentationScope', id: 1 }, spans: { rule: 'repeated', type: 'Span', id: 2 } } },
    ResourceSpans: { fields: { resource: { type: 'Resource', id: 1 }, scopeSpans: { rule: 'repeated', type: 'ScopeSpans', id: 2 } } },
    ExportTraceServiceRequest: { fields: { resourceSpans: { rule: 'repeated', type: 'ResourceSpans', id: 1 } } },
  },
});
const ExportTraceServiceRequest = schema.lookupType('ExportTraceServiceRequest');

interface RawValue {
  stringValue?: string;
  boolValue?: boolean;
  intValue?: protobuf.Long | number | string;
  doubleValue?: number;
  arrayValue?: { values?: RawValue[] };
  kvlistValue?: { values?: RawKeyValue[] };
  bytesValue?: Uint8Array;
}
interface RawKeyValue {
  key: string;
  value?: RawValue;
}

function plainValue(raw: RawValue | undefined): unknown {
  if (raw === undefined) return null;
  if (raw.stringValue !== undefined) return raw.stringValue;
  if (raw.boolValue !== undefined) return raw.boolValue;
  if (raw.intValue !== undefined) return Number(raw.intValue.toString());
  if (raw.doubleValue !== undefined) return raw.doubleValue;
  if (raw.arrayValue !== undefined) return (raw.arrayValue.values ?? []).map(plainValue);
  if (raw.kvlistValue !== undefined) return plainAttributes(raw.kvlistValue.values);
  if (raw.bytesValue !== undefined) return Buffer.from(raw.bytesValue).toString('hex');
  return null;
}

function plainAttributes(attributes: RawKeyValue[] | undefined): Record<string, unknown> {
  return Object.fromEntries((attributes ?? []).map((pair) => [pair.key, plainValue(pair.value)]));
}

/** A span the sink received, its attribute values as plain JS values. */
export interface ReceivedSpan {
  name: string;
  attributes: Record<string, unknown>;
  scope: { name: string; version: string };
  /** The exporting resource's attributes, `service.name` among them. */
  resource: Record<string, unknown>;
}

interface RawTraceRequest {
  resourceSpans?: {
    resource?: { attributes?: RawKeyValue[] };
    scopeSpans?: { scope?: { name?: string; version?: string }; spans?: { name?: string; attributes?: RawKeyValue[] }[] }[];
  }[];
}

/** Decodes a trace export's body into its spans. */
export function decodeSpans(body: Uint8Array): ReceivedSpan[] {
  const request = ExportTraceServiceRequest.toObject(ExportTraceServiceRequest.decode(body), { longs: String }) as RawTraceRequest;
  return (request.resourceSpans ?? []).flatMap((resourceSpans) =>
    (resourceSpans.scopeSpans ?? []).flatMap((scopeSpans) =>
      (scopeSpans.spans ?? []).map((span) => ({
        name: span.name ?? '',
        attributes: plainAttributes(span.attributes),
        scope: { name: scopeSpans.scope?.name ?? '', version: scopeSpans.scope?.version ?? '' },
        resource: plainAttributes(resourceSpans.resource?.attributes),
      })),
    ),
  );
}

/** A loopback OTLP/HTTP receiver. */
export interface OtlpSink {
  /** The base URL an exporter takes as `OTEL_EXPORTER_OTLP_ENDPOINT`; it appends the signal path itself. */
  endpoint: string;
  /** Every span exported so far, in arrival order. */
  spans(): ReceivedSpan[];
  close(): Promise<void>;
}

function bodyOf(request: http.IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    request.on('data', (chunk: Buffer) => chunks.push(chunk));
    request.once('error', reject);
    request.once('end', () => {
      const body = Buffer.concat(chunks);
      resolve(request.headers['content-encoding'] === 'gzip' ? zlib.gunzipSync(body) : body);
    });
  });
}

/**
 * Accepts trace, metric, and log exports with `200`, anything else with `404`. Only trace exports are decoded; the
 * other two are acknowledged and dropped.
 */
export async function startOtlpSink(): Promise<OtlpSink> {
  const received: ReceivedSpan[] = [];
  const server = http.createServer((request, response) => {
    const path = (request.url ?? '').split('?')[0] ?? '';
    if (request.method !== 'POST' || !EXPORT_PATHS.includes(path)) {
      request.resume();
      response.writeHead(404, { 'content-length': '0' }).end();
      return;
    }
    bodyOf(request).then(
      (body) => {
        try {
          if (path === TRACES_PATH) received.push(...decodeSpans(body));
          response.writeHead(200, { 'content-type': 'application/x-protobuf', 'content-length': '0' }).end();
        } catch {
          response.writeHead(400, { 'content-length': '0' }).end();
        }
      },
      () => response.writeHead(400, { 'content-length': '0' }).end(),
    );
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address() as { port: number };
  return {
    endpoint: `http://127.0.0.1:${port}`,
    spans: () => [...received],
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
