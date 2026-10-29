import * as http from 'node:http';
import * as zlib from 'node:zlib';
import * as protobuf from 'protobufjs';

const TRACES_PATH = '/v1/traces';
const METRICS_PATH = '/v1/metrics';
const EXPORT_PATHS = [TRACES_PATH, METRICS_PATH, '/v1/logs'];

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
    Span: {
      fields: {
        traceId: { type: 'bytes', id: 1 },
        spanId: { type: 'bytes', id: 2 },
        name: { type: 'string', id: 5 },
        attributes: { rule: 'repeated', type: 'KeyValue', id: 9 },
      },
    },
    ScopeSpans: { fields: { scope: { type: 'InstrumentationScope', id: 1 }, spans: { rule: 'repeated', type: 'Span', id: 2 } } },
    ResourceSpans: { fields: { resource: { type: 'Resource', id: 1 }, scopeSpans: { rule: 'repeated', type: 'ScopeSpans', id: 2 } } },
    ExportTraceServiceRequest: { fields: { resourceSpans: { rule: 'repeated', type: 'ResourceSpans', id: 1 } } },
    NumberDataPoint: {
      fields: {
        startTimeUnixNano: { type: 'fixed64', id: 2 },
        timeUnixNano: { type: 'fixed64', id: 3 },
        asDouble: { type: 'double', id: 4 },
        asInt: { type: 'sfixed64', id: 6 },
        attributes: { rule: 'repeated', type: 'KeyValue', id: 7 },
      },
    },
    Sum: {
      fields: {
        dataPoints: { rule: 'repeated', type: 'NumberDataPoint', id: 1 },
        aggregationTemporality: { type: 'int32', id: 2 },
        isMonotonic: { type: 'bool', id: 3 },
      },
    },
    Metric: {
      fields: { name: { type: 'string', id: 1 }, description: { type: 'string', id: 2 }, unit: { type: 'string', id: 3 }, sum: { type: 'Sum', id: 7 } },
    },
    ScopeMetrics: { fields: { scope: { type: 'InstrumentationScope', id: 1 }, metrics: { rule: 'repeated', type: 'Metric', id: 2 } } },
    ResourceMetrics: { fields: { resource: { type: 'Resource', id: 1 }, scopeMetrics: { rule: 'repeated', type: 'ScopeMetrics', id: 2 } } },
    ExportMetricsServiceRequest: { fields: { resourceMetrics: { rule: 'repeated', type: 'ResourceMetrics', id: 1 } } },
    LogRecord: {
      fields: {
        timeUnixNano: { type: 'fixed64', id: 1 },
        severityNumber: { type: 'int32', id: 2 },
        severityText: { type: 'string', id: 3 },
        body: { type: 'AnyValue', id: 5 },
        attributes: { rule: 'repeated', type: 'KeyValue', id: 6 },
        flags: { type: 'fixed32', id: 8 },
        traceId: { type: 'bytes', id: 9 },
        spanId: { type: 'bytes', id: 10 },
        observedTimeUnixNano: { type: 'fixed64', id: 11 },
      },
    },
    ScopeLogs: { fields: { scope: { type: 'InstrumentationScope', id: 1 }, logRecords: { rule: 'repeated', type: 'LogRecord', id: 2 } } },
    ResourceLogs: { fields: { resource: { type: 'Resource', id: 1 }, scopeLogs: { rule: 'repeated', type: 'ScopeLogs', id: 2 } } },
    ExportLogsServiceRequest: { fields: { resourceLogs: { rule: 'repeated', type: 'ResourceLogs', id: 1 } } },
  },
});
const ExportTraceServiceRequest = schema.lookupType('ExportTraceServiceRequest');
const ExportMetricsServiceRequest = schema.lookupType('ExportMetricsServiceRequest');
const ExportLogsServiceRequest = schema.lookupType('ExportLogsServiceRequest');

/** The OTLP/JSON form of an export body: the protobuf mapping with hex ids, where the protobuf JSON mapping's are base64. */
export function otlpJson(signal: 'metrics' | 'logs', body: Uint8Array): string {
  const type = signal === 'metrics' ? ExportMetricsServiceRequest : ExportLogsServiceRequest;
  const hexIds = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(hexIds);
    if (node === null || typeof node !== 'object' || node instanceof Uint8Array) return node;
    return Object.fromEntries(
      Object.entries(node).map(([key, value]) => [key, value instanceof Uint8Array && ['traceId', 'spanId', 'parentSpanId'].includes(key) ? Buffer.from(value).toString('hex') : hexIds(value)]),
    );
  };
  return JSON.stringify(hexIds(type.toObject(type.decode(body), { longs: String })));
}

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
  /** The span's trace id, hex. */
  traceId: string;
  attributes: Record<string, unknown>;
  scope: { name: string; version: string };
  /** The exporting resource's attributes, `service.name` among them. */
  resource: Record<string, unknown>;
}

interface RawTraceRequest {
  resourceSpans?: {
    resource?: { attributes?: RawKeyValue[] };
    scopeSpans?: { scope?: { name?: string; version?: string }; spans?: { name?: string; traceId?: Uint8Array; attributes?: RawKeyValue[] }[] }[];
  }[];
}

/** Decodes a trace export's body into its spans. */
export function decodeSpans(body: Uint8Array): ReceivedSpan[] {
  const request = ExportTraceServiceRequest.toObject(ExportTraceServiceRequest.decode(body), { longs: String }) as RawTraceRequest;
  return (request.resourceSpans ?? []).flatMap((resourceSpans) =>
    (resourceSpans.scopeSpans ?? []).flatMap((scopeSpans) =>
      (scopeSpans.spans ?? []).map((span) => ({
        name: span.name ?? '',
        traceId: Buffer.from(span.traceId ?? []).toString('hex'),
        attributes: plainAttributes(span.attributes),
        scope: { name: scopeSpans.scope?.name ?? '', version: scopeSpans.scope?.version ?? '' },
        resource: plainAttributes(resourceSpans.resource?.attributes),
      })),
    ),
  );
}

/** A metric data point or log record the sink received: its own attributes, and its resource's. */
export interface ReceivedItem {
  attributes: Record<string, unknown>;
  resource: Record<string, unknown>;
}

interface RawMetricRequest {
  resourceMetrics?: { resource?: { attributes?: RawKeyValue[] }; scopeMetrics?: { metrics?: { sum?: { dataPoints?: { attributes?: RawKeyValue[] }[] } }[] }[] }[];
}
interface RawLogRequest {
  resourceLogs?: { resource?: { attributes?: RawKeyValue[] }; scopeLogs?: { logRecords?: { attributes?: RawKeyValue[] }[] }[] }[];
}

/** Decodes a metric export's body into its sum data points. */
export function decodeSumPoints(body: Uint8Array): ReceivedItem[] {
  const request = ExportMetricsServiceRequest.toObject(ExportMetricsServiceRequest.decode(body), { longs: String }) as RawMetricRequest;
  return (request.resourceMetrics ?? []).flatMap((resourceMetrics) =>
    (resourceMetrics.scopeMetrics ?? []).flatMap((scopeMetrics) =>
      (scopeMetrics.metrics ?? []).flatMap((metric) =>
        (metric.sum?.dataPoints ?? []).map((point) => ({
          attributes: plainAttributes(point.attributes),
          resource: plainAttributes(resourceMetrics.resource?.attributes),
        })),
      ),
    ),
  );
}

/** Decodes a log export's body into its records. */
export function decodeLogRecords(body: Uint8Array): ReceivedItem[] {
  const request = ExportLogsServiceRequest.toObject(ExportLogsServiceRequest.decode(body), { longs: String }) as RawLogRequest;
  return (request.resourceLogs ?? []).flatMap((resourceLogs) =>
    (resourceLogs.scopeLogs ?? []).flatMap((scopeLogs) =>
      (scopeLogs.logRecords ?? []).map((record) => ({
        attributes: plainAttributes(record.attributes),
        resource: plainAttributes(resourceLogs.resource?.attributes),
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
  /** Every sum data point exported so far, in arrival order. */
  sumPoints(): ReceivedItem[];
  /** Every log record exported so far, in arrival order. */
  logRecords(): ReceivedItem[];
  /** The connections open on the sink now; a hung sink holds every one it accepted until the exporter drops it. */
  openConnections(): number;
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
 * Accepts trace, metric, and log exports with `200`, anything else with `404`. Traces decode to spans, metrics to their
 * sum data points, logs to their records. `hang` accepts each connection and reads each request, and never answers.
 */
export async function startOtlpSink(options: { hang?: boolean } = {}): Promise<OtlpSink> {
  const received: ReceivedSpan[] = [];
  const points: ReceivedItem[] = [];
  const records: ReceivedItem[] = [];
  let open = 0;
  const server = http.createServer((request, response) => {
    if (options.hang === true) {
      request.resume();
      return;
    }
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
          else if (path === METRICS_PATH) points.push(...decodeSumPoints(body));
          else records.push(...decodeLogRecords(body));
          response.writeHead(200, { 'content-type': 'application/x-protobuf', 'content-length': '0' }).end();
        } catch {
          response.writeHead(400, { 'content-length': '0' }).end();
        }
      },
      () => response.writeHead(400, { 'content-length': '0' }).end(),
    );
  });
  // Node answers a request still unanswered at `requestTimeout` with a 408; a hung sink never answers at all.
  if (options.hang === true) server.requestTimeout = 0;
  server.on('connection', (socket) => {
    open++;
    socket.once('close', () => open--);
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address() as { port: number };
  return {
    endpoint: `http://127.0.0.1:${port}`,
    spans: () => [...received],
    sumPoints: () => [...points],
    logRecords: () => [...records],
    openConnections: () => open,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
