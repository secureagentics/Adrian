// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

/**
 * Starter templates for the Agent Profile editor.
 *
 * The user picks one to prefill the remit + expected-behaviours +
 * known-risks fields, then customises freely. Templates exist purely
 * to lower the blank-page cost of writing a custom policy - they're
 * not policy themselves until the user saves.
 */

export interface AgentProfileTemplate {
  id: string
  name: string
  description: string
  remit: string
  /** Behaviours the agent is *supposed* to perform - stay-in-lane signals. */
  expectedBehaviours: string[]
  /** Behaviours the agent should *never* perform - flag-this signals. */
  knownRisks: string[]
}

export const AGENT_PROFILE_TEMPLATES: AgentProfileTemplate[] = [
  {
    id: 'customer_service',
    name: 'Customer Service Agent',
    description:
      'Inbound user queries, knowledge-base retrieval, ticketing-system reads/writes, defined-trigger escalation to humans.',
    remit:
      'Handles inbound user queries by retrieving from a knowledge base, reading and writing ticketing systems, and escalating to humans on defined triggers.',
    expectedBehaviours: [
      'Retrieving from a defined knowledge base or FAQ vector store in response to user queries',
      'Reading, creating and updating tickets in a CRM or service desk (Zendesk, Intercom, Salesforce Service Cloud)',
      'Sending templated or LLM-generated replies to the originating customer only, within a single conversation thread',
    ],
    knownRisks: [
      'Sending data to email addresses or webhooks outside the originating ticket or conversation context',
      'Executing shell commands, code, or arbitrary HTTP requests to non-allowlisted endpoints',
      'Bulk-reading customer records beyond the scope of the active ticket (mass PII enumeration)',
    ],
  },
  {
    id: 'research_summarisation',
    name: 'Research & Summarisation Agent',
    description:
      'Multi-step web search, document retrieval and synthesis to produce summaries or briefings; read-only against external sources.',
    remit:
      'Performs multi-step web search, document retrieval and synthesis to produce summaries or briefings, with read-only access to external sources.',
    expectedBehaviours: [
      'Performing multi-step web search, fetch and document retrieval against external sources',
      'Reading from internal document stores, wikis or vector databases for grounding',
      'Producing synthesised text output returned to the requesting user',
    ],
    knownRisks: [
      'Any write operation to internal systems (CRM updates, ticket creation, file modification, database writes)',
      'Outbound communications on behalf of the user (email send, Slack post, calendar invite)',
      'Fetching URLs supplied in retrieved content rather than user-supplied or allowlisted (indirect prompt injection)',
    ],
  },
  {
    id: 'coding_assistant',
    name: 'Coding Assistant Agent',
    description:
      'Reads, writes and executes code against a developer repo and shell, with privileges to modify source, run tests and invoke build tools.',
    remit:
      "Reads, writes and executes code against a developer's repository and shell, with privileges to modify source files, run tests and invoke build tools.",
    expectedBehaviours: [
      'Reading, writing and modifying source files within a defined repository or workspace',
      'Executing shell commands, build scripts, package managers and test runners in a sandboxed environment',
      'Calling git operations (branch, commit, diff) and interacting with the local language toolchain',
    ],
    knownRisks: [
      'Pushing to protected branches, force-pushing, or modifying CI/CD pipeline definitions without explicit confirmation',
      'Reading or writing paths outside the designated repo or workspace (e.g. ~/.ssh, ~/.aws, system credential stores)',
      'Outbound network calls to non-development domains (no reason for a coding agent to email, post to social, or call a CRM)',
    ],
  },
  {
    id: 'sales_outbound',
    name: 'Sales & Outbound Agent',
    description:
      'CRM and prospect-data reads, third-party enrichment, outbound communications (email, LinkedIn) at volume on behalf of a sales user.',
    remit:
      'Reads CRM and prospect data, enriches from third-party sources, and sends outbound communications (email, LinkedIn) at volume on behalf of a sales user.',
    expectedBehaviours: [
      'Reading and updating CRM records (contacts, accounts, opportunities, activity logs)',
      'Enriching prospect data from defined third-party sources (LinkedIn, ZoomInfo, Apollo, Clearbit)',
      'Sending outbound email or messaging via a configured sending platform, within defined volume and cadence rules',
    ],
    knownRisks: [
      'Sending to recipient addresses or domains not derived from the configured CRM or enrichment sources (data-exfil pattern)',
      'Reading CRM data outside the assigned territory, segment or user scope',
      'Modifying or deleting CRM records beyond activity logging (e.g. mass updates to opportunity stages, contact deletions)',
    ],
  },
  {
    id: 'data_analyst',
    name: 'Data Analyst Agent',
    description:
      'Translates natural-language questions into SQL or analytical queries against structured data stores; typically read-only against production data.',
    remit:
      'Translates natural-language questions into SQL or analytical queries against structured data stores and returns results, typically read-only against production data.',
    expectedBehaviours: [
      'Generating and executing read-only SQL or analytical queries against defined data warehouses or BI tools',
      'Reading schema, table metadata and sample rows for query planning',
      'Returning query results, summaries or visualisations to the requesting user',
    ],
    knownRisks: [
      'Executing write DML or DDL statements (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE)',
      "Querying tables outside the user's permission scope or data-product boundary",
      'Exporting result sets to external destinations (S3 buckets, email, file shares) rather than returning to the requesting user',
    ],
  },
]

/**
 * Limits mirrored from the dashboard-api validator.  Kept in sync by
 * hand: the server is authoritative; the dashboard validates client-
 * side only for immediate UX feedback.
 */
export const AGENT_PROFILE_LIMITS = {
  remitMaxChars: 500,
  entryMaxChars: 120,
  entryMaxCount: 10,
} as const
