// openapi-ts selects one media type per status. Combine the declared schemas
// in its input so a generated error includes every actual response variant.
export function responseUnionInput(spec) {
  for (const methods of Object.values(spec.paths)) {
    for (const operation of Object.values(methods)) {
      for (const response of Object.values(operation.responses ?? {})) {
        const content = response.content;
        const json = content?.['application/json'];
        const problem = content?.['application/problem+json'];
        if (json?.schema && problem?.schema) {
          json.schema = { anyOf: [json.schema, problem.schema] };
        }
      }
    }
  }
}
