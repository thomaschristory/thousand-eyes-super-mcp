# Tool splitting

ThousandEyes' OpenAPI tag taxonomy (`Tests`, `Agent to Server Tests`,
`BGP Tests`, `Test Results`, `Alerts`, `Agents`, `Dashboards`, …) is wide
and uneven. A naive "one tool per tag" registration would expose a handful
of huge tools that overwhelm the LLM. A "one tool per operation" approach
would explode the tool count. The loader splits adaptively.

## Algorithm

Given `max_actions_per_tool = N` (default 80):

1. Group operations by **section** — the first component of the tag
   (everything before " - " if a hierarchy delimiter is present).
2. For each section:
    - If it has ≤ N operations, emit one tool named after the section.
    - Otherwise split by **sub-tag** (second component of the tag).
        - For each sub-tag, if it has ≤ N operations, emit one tool
          `<section>_<subtag>`.
        - If a sub-tag is still over N, recurse on URL **path depth**,
          starting at depth 3 and deepening one segment at a time until
          every bucket is ≤ N or depth 5 is reached. Emit one tool per
          leaf bucket.
3. Sibling buckets with fewer than 4 ops collapse into `<parent>_misc`.
4. Any tool still over N at max depth logs a `WARNING` and is emitted anyway.

## Disabling the splitter

Set `max_actions_per_tool: 0` in `thousand-eyes-mcp.yaml` (or
`--max-actions-per-tool 0` on the CLI). Every section becomes one tool.
Convenient for spec exploration with `--diff`, hostile to the LLM at
runtime.

## Tuning

- **Smaller N** (e.g. 40) — more tools, each tighter. Better when the
  LLM's tool-selection model gets confused by ambiguous large tools.
- **Larger N** (e.g. 200) — fewer tools. Better when the LLM has good
  per-action descriptions and you want to minimise the registration
  overhead.

The default of 80 is a balance struck across the bundled v7.0.88 spec.
