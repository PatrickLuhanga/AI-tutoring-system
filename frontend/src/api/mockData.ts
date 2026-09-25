/**
 * Mock payloads for the frontend-first prototype.
 *
 * Every object here is shaped exactly like the Flask API responses so that
 * `src/api/client.ts` can be flipped from `MOCK` to real HTTP without any
 * component changes.
 */

import type {
  ChatMessage,
  ChatResponse,
  LLMConfig,
  Module,
  OllamaModelsResponse,
  TelemetryAnalytics,
} from '../types'

export const MOCK = true

/** Mirrors MODULE_REGISTRY in src/config.py. */
export const MODULES: Module[] = [
  {
    module_id: 'IPRT301',
    module_code: 'IPRT301',
    module_name: 'Internet Programming',
    course_code: 'DIP3',
    language: 'Java',
  },
  {
    module_id: 'PBDV301',
    module_code: 'PBDV301',
    module_name: 'Platform Based Development',
    course_code: 'DIP3',
    language: 'Python',
  },
  {
    module_id: 'RESK301',
    module_code: 'RESK301',
    module_name: 'Research Skills',
    course_code: 'DIP3',
    language: 'N/A',
  },
  {
    module_id: 'SPRI301',
    module_code: 'SPRI301',
    module_name: 'Social and Professional Issues',
    course_code: 'DIP3',
    language: 'N/A',
  },
]

// ---------------------------------------------------------------------------
// Mock transcript — a student debugging an IndexOutOfRange-style error.
// ---------------------------------------------------------------------------

const C_SHARP_HINT = `Let's slow down and look at **how you are indexing**, not at the fix itself.

When you write \`numbers[i]\`, C# requires \`i\` to satisfy \`0 <= i < numbers.Length\`. The
exception tells you that assumption broke somewhere, so the real question is: *which value of
\`i\` walked past the end of the array?*

Here is the smallest version of your loop so we can reason about it together:

\`\`\`csharp
int[] numbers = { 4, 8, 15, 16 };

// Where does this loop read past the last valid index?
for (int i = 0; i <= numbers.Length; i++)
{
    Console.WriteLine(numbers[i]);
}
\`\`\`

### Questions to work through
1. What is \`numbers.Length\` for this array?
2. What is the **last** valid index — and how is it related to \`Length\`?
3. Which iteration of the loop first breaks that rule?

Change *one* boundary condition in your own code, run it again, and paste the output (or the
new exception). I'll stay on the loop boundary with you until it compiles and prints every
element exactly once.`

const C_SHARP_FOLLOWUP = `Good — you spotted that \`i <= numbers.Length\` lets \`i\` reach \`Length\`, which is one
past the last element. That is the classic off-by-one.

Before you commit the fix, predict the output for this array so you can prove the boundary is
right in your head:

\`\`\`csharp
int[] numbers = { 2, 4, 6 };
\`\`\`

How many lines do you expect to print, and what are they? If that matches what your loop
prints, the bug is genuinely gone — not just hidden.`

export const INITIAL_MESSAGES: ChatMessage[] = [
  {
    message_id: 'seed-msg-0001',
    role: 'user',
    content:
      "I keep getting `System.IndexOutOfRangeException: Index was outside the bounds of the array` when I loop through my array in C#. I've been staring at it for an hour. Can you just show me the correct code?",
    created_at: '2026-09-24T09:12:00Z',
  },
  {
    message_id: 'seed-msg-0002',
    role: 'assistant',
    content: C_SHARP_HINT,
    created_at: '2026-09-24T09:12:04Z',
    audit: {
      intent: { label: 'debugging', confidence: 0.91, source: 'heuristic' },
      scaffolding: { stage: 'guiding', hint_depth: 1, strategy: 'questioning' },
      guardrail: { flagged: false, flags: [], action: 'pass' },
      retrieval: {
        query: 'IndexOutOfRangeException loop array C# boundary',
        module_id: 'PBDV301',
        chunks: [
          {
            chunk_id: 412,
            module_id: 'PBDV301',
            source_name: 'Arrays & Collections',
            section_title: 'Bounds checking and off-by-one loops',
            distance: 0.1873,
            preview:
              'An array of length n exposes valid indices 0 through n-1. Iterating with i <= n reads one element past the end...',
          },
        ],
        patterns: [
          {
            pattern_id: 88,
            module_id: null,
            error_title: 'Loop boundary reads one past the final element',
            exception_thrown: 'System.IndexOutOfRangeException',
            distance: 0.1542,
            hint: 'Ask the student which iteration first violates 0 <= i < Length rather than correcting the operator.',
          },
        ],
      },
      llm: { provider: 'local', model: 'qwen3:4b', backend: 'ollama', latency_ms: 1840 },
      telemetry_log_id: 90211,
    },
  },
  {
    message_id: 'seed-msg-0003',
    role: 'user',
    content: 'Oh. `i <= numbers.Length` should be `i < numbers.Length`, right?',
    created_at: '2026-09-24T09:14:31Z',
  },
  {
    message_id: 'seed-msg-0004',
    role: 'assistant',
    content: C_SHARP_FOLLOWUP,
    created_at: '2026-09-24T09:14:35Z',
    feedback: { rating: 1, submitted: true },
    audit: {
      intent: { label: 'debugging', confidence: 0.95, source: 'heuristic' },
      scaffolding: { stage: 'reinforcing', hint_depth: 2, strategy: 'questioning' },
      guardrail: { flagged: false, flags: [], action: 'pass' },
      retrieval: {
        query: 'off by one loop boundary verify prediction C#',
        module_id: 'PBDV301',
        chunks: [],
        patterns: [],
      },
      llm: { provider: 'local', model: 'qwen3:4b', backend: 'ollama', latency_ms: 1210 },
      telemetry_log_id: 90218,
    },
  },
]

/** Canned replies keyed by rough intent, cycled when the student sends a message. */
const CANNED_REPLIES: string[] = [
  `That's a useful observation — let's test it rather than trust it.

> What output do you *expect* from the code as it stands, and what does it actually print?

Write down both, then run it. The gap between them is the clue we care about.

\`\`\`csharp
for (int i = 0; i < numbers.Length; i++)
{
    Console.WriteLine($"[{i}] = {numbers[i]}");
}
\`\`\`

If the run matches your prediction, tell me the predicted output so I can confirm your mental
model is now correct.`,

  `You're close. Before we move on, try to explain *why* the boundary rule exists, not just what
it is.

1. If \`Length\` is \`n\`, how many elements can you address?
2. Why would starting at \`1\` also be a bug?

Answer those in your own words and I'll verify your reasoning rather than your syntax.`,
]

let cannedCursor = 0

/** Build a mock `ChatResponse` for a student turn. */
export function mockChatResponse(message: string, sessionId: string, moduleId: string): ChatResponse {
  const reply = CANNED_REPLIES[cannedCursor % CANNED_REPLIES.length]
  cannedCursor += 1

  const bypass =
    /\b(give me (the )?(answer|code|solution)|just give me|final code|solve it for me)\b/i.test(
      message,
    )

  return {
    session_id: sessionId,
    message_id: `mock-${Date.now().toString(36)}-${cannedCursor}`,
    reply: bypass
      ? `I can't hand over a finished solution — that would skip the part where you learn.

Let's meet in the middle: describe the **exact error message** and the line it points to, and
I'll ask you one guiding question at a time. What does your debugger say the value of the index
is right before the failure?`
      : reply,
    intent: {
      label: bypass ? 'bypass' : 'debugging',
      confidence: bypass ? 0.88 : 0.9,
      source: 'heuristic',
    },
    scaffolding: {
      stage: 'guiding',
      hint_depth: 1,
      strategy: 'questioning',
    },
    guardrail: {
      flagged: bypass,
      flags: bypass ? ['bypass_attempt'] : [],
      action: bypass ? 'blocked' : 'pass',
    },
    retrieval: {
      query: message.slice(0, 120),
      module_id: moduleId,
      chunks: [
        {
          chunk_id: 417,
          module_id: moduleId,
          source_name: 'Debugging Fundamentals',
          section_title: 'Reading a stack trace as evidence',
          distance: 0.221,
          preview:
            'Start from the innermost frame and work outward: the exception type names the category of failure, the message names the value, the frame names the line...',
        },
      ],
      patterns: [],
    },
    llm: {
      provider: 'local',
      model: 'qwen3:4b',
      backend: 'ollama',
      latency_ms: 1520,
    },
    telemetry_log_id: Math.floor(Math.random() * 100000),
    identity: { student_id: 1042, email: 'student@example.edu', role: 'student' },
    module_access: { allowed: true, exists: true },
  }
}

// ---------------------------------------------------------------------------
// Dynamic LLM Router mocks
// ---------------------------------------------------------------------------

export const MOCK_LLM_CONFIG: LLMConfig = {
  config_id: 1,
  name: 'default',
  is_active: true,
  provider: 'local',
  local: { base_url: 'http://localhost:11434', model: 'qwen3:4b' },
  cloud: {
    provider: 'openai',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    has_api_key: true,
    api_key_masked: '....4f2a',
  },
  generation: { temperature: 0.4, max_tokens: 900, top_p: 0.9 },
  effective: { target: 'ollama', model: 'qwen3:4b' },
  updated_by: 'admin@example.edu',
  updated_at: '2026-09-23T16:41:00Z',
}

export const MOCK_OLLAMA_MODELS: OllamaModelsResponse = {
  base_url: 'http://localhost:11434',
  count: 4,
  models: [
    {
      name: 'qwen3:4b',
      model: 'qwen3:4b',
      size_bytes: 2_500_000_000,
      size_gb: 2.5,
      modified_at: '2026-09-20T10:02:00Z',
      family: 'qwen3',
      parameter_size: '4B',
    },
    {
      name: 'qwen2.5-coder:7b',
      model: 'qwen2.5-coder:7b',
      size_bytes: 4_700_000_000,
      size_gb: 4.7,
      modified_at: '2026-09-18T08:11:00Z',
      family: 'qwen2',
      parameter_size: '7B',
    },
    {
      name: 'deepseek-r1:7b',
      model: 'deepseek-r1:7b',
      size_bytes: 4_900_000_000,
      size_gb: 4.9,
      modified_at: '2026-09-15T20:33:00Z',
      family: 'deepseek',
      parameter_size: '7B',
    },
    {
      name: 'llama3.2:3b',
      model: 'llama3.2:3b',
      size_bytes: 2_000_000_000,
      size_gb: 2.0,
      modified_at: '2026-09-12T13:05:00Z',
      family: 'llama',
      parameter_size: '3B',
    },
  ],
}

// ---------------------------------------------------------------------------
// Telemetry analytics mocks — the "LLM Health Index"
// ---------------------------------------------------------------------------

export const MOCK_ANALYTICS: TelemetryAnalytics = {
  total_sessions: 1284,
  total_hints: 6721,
  satisfaction: { thumbs_up: 5340, thumbs_down: 1381 },
  failure_categories: [
    { reason_tag: 'too_confusing', label: 'Still stuck', count: 512 },
    { reason_tag: 'too_long_or_too_short', label: 'Too long / too short', count: 388 },
    { reason_tag: 'gave_away_answer', label: 'Gave away answer', count: 241 },
    { reason_tag: 'incorrect_answer', label: 'Incorrect answer', count: 240 },
  ],
  by_module: [
    { module_id: 'IPRT301', module_name: 'Internet Programming', satisfaction_rate: 0.82, responses: 2180 },
    { module_id: 'PBDV301', module_name: 'Platform Based Development', satisfaction_rate: 0.77, responses: 1940 },
    { module_id: 'RESK301', module_name: 'Research Skills', satisfaction_rate: 0.88, responses: 1320 },
    { module_id: 'SPRI301', module_name: 'Social and Professional Issues', satisfaction_rate: 0.9, responses: 1281 },
  ],
  average_latency_ms: 1633,
}
