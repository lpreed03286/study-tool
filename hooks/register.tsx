import type { EngineInterface, Register } from 'claude-code'

const PLUGIN = 'math-worksheet'
// The first folder is where an existing install keeps its data, so it stays in use.
const DATA_FOLDERS = ['.local/share/mae692-worksheets', '.local/share/math-worksheet']
const PYTHON_PACKAGES = ['sympy==1.14.0', 'mpmath>=1.3,<2']
const ASK_MODEL = 'opus'
const ASK_TIMEOUT_MS = 120_000
const START_TIMEOUT_MS = 30_000

type Server = { port: number; token: string; url: string }
type ReadyEvent = Server & { event: 'ready' }
type AskEvent = { event: 'ask'; id: string; system: string; prompt: string }
type PromptEvent = { event: 'prompt'; id: string; text: string }
type ServerEvent = ReadyEvent | AskEvent | PromptEvent

type ToolDefinition = {
  name: string
  description: string
  inputSchema: Record<string, unknown>
}

const BOOK = {
  type: 'object',
  description: 'The textbook',
  properties: {
    name: { type: 'string', description: 'e.g. Kreyszig, Advanced Engineering Mathematics' },
    edition: { type: 'string', description: 'e.g. 10th' },
    source: { type: 'string', enum: ['pdf', 'memory'] },
    sections: {
      type: 'array',
      items: { type: 'object', properties: { section: { type: 'string' }, topic: { type: 'string' } } },
    },
  },
  required: ['name'],
}

const SOURCES = {
  type: 'array',
  description: 'Pages that say this. The server opens each one and looks for the quote. See the make-doc skill.',
  items: {
    type: 'object',
    properties: {
      title: { type: 'string' },
      url: { type: 'string', description: 'A page you opened in this session, never one from memory' },
      file: { type: 'string', description: "Instead of url: a path to the student's PDF or image" },
      library: { type: 'string', description: 'Instead of url: a library file id. Give its page too' },
      page: { type: 'number', description: 'The PDF page number' },
      quote: { type: 'string', description: 'A word-for-word passage from the source, 4 to 60 words' },
    },
    required: ['title', 'quote'],
  },
}
const OWN_WORDS = { type: 'boolean', description: "True for your own explanation or analogy, which the page labels as having no source" }

const TOOLS: ToolDefinition[] = [
  {
    name: 'check_coverage',
    description:
      'First step for any worksheet. For a textbook and its sections, returns what the repertoire check recorded for each section, and every method with what it handles and its limits. See the make-set skill.',
    inputSchema: {
      type: 'object',
      properties: {
        textbook: BOOK,
        sections: {
          type: 'array',
          items: {
            type: 'object',
            properties: { section: { type: 'string' }, topic: { type: 'string' } },
            required: ['section'],
          },
        },
      },
      required: ['textbook', 'sections'],
    },
  },
  {
    name: 'add_method',
    description:
      'Validates a new method file written to the repertoire folder check_coverage names. It joins the repertoire only if every sample passes full verification. See the make-set skill for the format.',
    inputSchema: {
      type: 'object',
      properties: { file: { type: 'string', description: 'File name inside the repertoire folder, e.g. reduction_of_order.py' } },
      required: ['file'],
    },
  },
  {
    name: 'record_coverage',
    description:
      'Records that repertoire methods fit textbook sections. create_set refuses any section without this record.',
    inputSchema: {
      type: 'object',
      properties: {
        textbook: BOOK,
        entries: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              section: { type: 'string' },
              topic: { type: 'string' },
              methods: { type: 'array', items: { type: 'string' } },
              rationale: { type: 'string', description: 'Why these methods teach this section the way the book does' },
            },
            required: ['section', 'methods', 'rationale'],
          },
        },
      },
      required: ['textbook', 'entries'],
    },
  },
  {
    name: 'study_plan',
    description:
      "How many problems of each method to generate, weighted toward the student's weak step types from the error log.",
    inputSchema: {
      type: 'object',
      properties: { methods: { type: 'array', items: { type: 'string' } }, count: { type: 'number' } },
      required: ['methods', 'count'],
    },
  },
  {
    name: 'error_log',
    description: "The student's full error log: misses and hint use for each step type.",
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'create_set',
    description:
      'Builds a worksheet set. The server computes every answer key with SymPy and verifies it. All or nothing: a problem that fails is reported in `failed` and nothing is saved. Never write answers yourself. See the make-set skill.',
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        textbook: BOOK,
        origin: { type: 'string', enum: ['generated', 'typed'] },
        difficulty: {
          type: 'string',
          enum: ['textbook', 'easier', 'harder', 'challenge'],
          description: 'How hard the generated problems are; textbook unless the student asked otherwise. See the make-set skill.',
        },
        request_id: { type: 'string', description: "From the worksheet page's New form, when the request names one" },
        weak_spots_used: { type: 'array', items: { type: 'string' } },
        book_methods: {
          type: 'object',
          description:
            'Method id to { summary, notation, step_notes, source, sources, own_words }: how the book presents each method, so hints and explanations follow it. See the make-set skill.',
        },
        problems: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              statement: { type: 'string', description: "LaTeX, e.g. y''+3y'+2y=3e^{2x}" },
              ic: { type: 'array', items: { type: 'string' }, description: "e.g. ['y(0)=1']" },
              method: { type: 'string' },
              section: { type: 'string' },
              topic: { type: 'string' },
              questions: {
                type: 'array',
                description: 'For concept_questions only. See the make-set skill.',
                items: {
                  type: 'object',
                  properties: {
                    question: { type: 'string' },
                    answer: { type: 'string', description: 'The reference answer' },
                    nudge: { type: 'string' },
                    method_hint: { type: 'string' },
                    sources: SOURCES,
                    own_words: OWN_WORDS,
                  },
                  required: ['question', 'answer', 'nudge', 'method_hint'],
                },
              },
            },
            required: ['statement', 'method', 'section', 'topic'],
          },
        },
      },
      required: ['title', 'textbook', 'problems'],
    },
  },
  {
    name: 'create_doc',
    description:
      'Saves a study guide or equation sheet. Plots are computed from their formulas and examples with a method are verified with SymPy. All or nothing: a block that fails is reported in `failed` and nothing is saved. See the make-doc skill.',
    inputSchema: {
      type: 'object',
      properties: {
        kind: { type: 'string', enum: ['study_guide', 'equation_sheet'] },
        title: { type: 'string' },
        subject: { type: 'string', description: 'The class or subject' },
        textbook: BOOK,
        source: { type: 'string', enum: ['pdf', 'memory'] },
        request_id: { type: 'string', description: "From the worksheet page's New form, when the request names one" },
        book_methods: { type: 'object', description: 'Method id to { notation }, as in create_set' },
        blocks: {
          type: 'array',
          description: 'What the document shows, in order. See the make-doc skill for each type.',
          items: {
            type: 'object',
            properties: {
              type: { type: 'string', enum: ['heading', 'text', 'list', 'equation', 'definition', 'nomenclature', 'table', 'note', 'plot', 'diagram', 'example'] },
              tab: { type: 'string', description: 'Also show this block in the quick reference tab with this name. See the make-doc skill.' },
              sources: SOURCES,
              own_words: OWN_WORDS,
            },
            required: ['type'],
          },
        },
      },
      required: ['kind', 'title', 'blocks'],
    },
  },
  {
    name: 'library_overview',
    description:
      "Start here for anything using the student's class library. With class_name, returns that class's overview: every file with its summary and topics by page. Without one, lists the classes. Costs few tokens, so use it instead of opening files.",
    inputSchema: { type: 'object', properties: { class_name: { type: 'string', description: 'A class name, number or both' } } },
  },
  {
    name: 'library_search',
    description: "Searches the saved text of the student's library, and gives the file, page and a snippet for each match. Every word must be on the page.",
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string' },
        class_name: { type: 'string' },
        file: { type: 'string', description: 'Limit the search to one file id' },
      },
      required: ['query'],
    },
  },
  {
    name: 'library_read',
    description:
      'Reads saved text from a library file, up to 40 pages at a time, without opening the file. With outline true, returns only the first line of each page. Use this rather than reading the PDF.',
    inputSchema: {
      type: 'object',
      properties: {
        file: { type: 'string' },
        pages: { type: 'array', items: { type: 'number' } },
        outline: { type: 'boolean' },
      },
      required: ['file'],
    },
  },
  {
    name: 'library_pending',
    description: 'Lists library files that need indexing, and any pages that are pictures to read once. See the make-index skill.',
    inputSchema: { type: 'object', properties: { files: { type: 'array', items: { type: 'string' } } } },
  },
  {
    name: 'library_save_index',
    description: "Saves a library file's summary, topics by page and descriptions of picture pages. See the make-index skill.",
    inputSchema: {
      type: 'object',
      properties: {
        file: { type: 'string' },
        summary: { type: 'string' },
        topics: { type: 'array', items: { type: 'object', properties: { topic: { type: 'string' }, pages: { type: 'string' } }, required: ['topic'] } },
        descriptions: { type: 'array', items: { type: 'object', properties: { page: { type: 'number' }, text: { type: 'string' } }, required: ['page', 'text'] } },
      },
      required: ['file', 'summary'],
    },
  },
  {
    name: 'open_worksheet',
    description: "Opens the worksheet window beside the terminal. Call it after create_set.",
    inputSchema: { type: 'object', properties: {} },
  },
]

// The running server, for this module's life. A reload stops the child and
// clears this; the next command or tool call starts it again.
let starting: Promise<Server> | undefined

async function exists($: EngineInterface, path: string): Promise<boolean> {
  return Boolean(await $.fs.stat(path).catch(() => undefined))
}

async function works($: EngineInterface, command: string[]): Promise<boolean> {
  const done = await $.process.run(command, { timeoutMs: 30_000 }).catch(() => undefined)

  return done?.exitCode === 0
}

// Nix if this computer has it, or else a virtual environment from its own Python.
async function buildRuntime($: EngineInterface, environment: string): Promise<void> {
  if (await works($, ['nix', '--version'])) {
    const built = await $.process.run(['nix', 'build', `path:${$.plugin.root}#default`, '-o', environment], { timeoutMs: 600_000 })
    if (built.exitCode === 0) return
  }
  const python = (await works($, ['python3', '--version'])) ? 'python3' : (await works($, ['python', '--version'])) ? 'python' : ''
  if (!python) throw new Error('this needs Python 3.10 or newer. Install it, then run /worksheet again')
  const venv = await $.process.run([python, '-m', 'venv', environment], { timeoutMs: 120_000 })
  if (venv.exitCode !== 0) throw new Error(`Python couldn't make a virtual environment: ${venv.stderr.slice(-400)}`)
  const pip = (await exists($, `${environment}/Scripts/python.exe`)) ? `${environment}/Scripts/python.exe` : `${environment}/bin/python`
  const packages = await $.process.run([pip, '-m', 'pip', 'install', ...PYTHON_PACKAGES], { timeoutMs: 600_000 })
  if (packages.exitCode !== 0) throw new Error(`pip install failed: ${packages.stderr.slice(-400)}`)
}

async function pythonPath($: EngineInterface): Promise<string> {
  const home = (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE'))
  const legacy = `${home}/${DATA_FOLDERS[0]}`
  const folder = (await exists($, legacy)) ? legacy : `${home}/${DATA_FOLDERS[1]}`
  const environment = `${folder}/python-env`
  const candidates = [`${environment}/bin/python3`, `${environment}/Scripts/python.exe`]
  for (const python of candidates) if (await exists($, python)) return python
  $.ui.status('math-worksheet: setting up Python, first run only')
  await buildRuntime($, environment)
  $.ui.status(undefined)
  for (const python of candidates) if (await exists($, python)) return python
  throw new Error('Python was set up, but its program wasn\'t where it was expected')
}

function runServer($: EngineInterface, python: string, dataFolder?: string): Promise<Server> {
  return new Promise<Server>((resolve, reject) => {
    const timer = $.clock.after(START_TIMEOUT_MS, () =>
      reject(new Error('the worksheet server did not start in time')),
    )
    void (async () => {
      let pending = ''
      let server: Server | undefined
      try {
        const child = $.process.spawn({
          argv: [
            python,
            '-B',
            `${$.plugin.root}/server/server.py`,
            ...(dataFolder ? ['--data', dataFolder] : []),
          ],
          env: { PYTHONDONTWRITEBYTECODE: '1' },
        })
        for await (const { stream, text } of child) {
          if (stream === 'stderr') {
            $.ui.log(text, { to: 'debug' })
            continue
          }
          pending += text
          let newline = pending.indexOf('\n')
          while (newline >= 0) {
            const line = pending.slice(0, newline)
            pending = pending.slice(newline + 1)
            newline = pending.indexOf('\n')
            const event = JSON.parse(line) as ServerEvent
            if (event.event === 'ready') {
              server = { port: event.port, token: event.token, url: event.url }
              timer.cancel()
              resolve(server)
            } else if (server) {
              if (event.event === 'prompt') void passRequestToClaude($, server, event)
              else void answerAsk($, server, event)
            }
          }
        }
        reject(new Error('the worksheet server stopped'))
      } catch (failure) {
        reject(failure instanceof Error ? failure : new Error(String(failure)))
      } finally {
        starting = undefined
        $.ui.status(undefined)
      }
    })()
  })
}

function startServer($: EngineInterface): Promise<Server> {
  if (starting) return starting
  starting = Promise.all([pythonPath($), $.env.get('MATH_WORKSHEET_DATA')]).then(([python, dataFolder]) =>
    runServer($, python, dataFolder),
  )
  starting.catch(() => {
    starting = undefined
  })

  return starting
}

async function post(
  $: EngineInterface,
  server: Server,
  path: string,
  body: unknown,
): Promise<{ ok: boolean; value: Record<string, unknown> }> {
  const reply = await $.http.fetch(`http://127.0.0.1:${server.port}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Worksheet-Token': server.token },
    body: JSON.stringify(body),
  })

  return { ok: reply.ok, value: JSON.parse(reply.text) as Record<string, unknown> }
}

async function answerAsk($: EngineInterface, server: Server, ask: AskEvent): Promise<void> {
  let text: string | undefined
  let error = ''
  try {
    const reply = await $.model.complete({
      model: ASK_MODEL,
      system: ask.system,
      prompt: ask.prompt,
      maxTokens: 1500,
      timeoutMs: ASK_TIMEOUT_MS,
    })
    if (reply.isAnswered) text = reply.text
    else error = reply.reason
  } catch (failure) {
    error = failure instanceof Error ? failure.message : String(failure)
  }
  await post($, server, '/internal/ask_result', { id: ask.id, text, error })
}

async function passRequestToClaude($: EngineInterface, server: Server, request: PromptEvent): Promise<void> {
  let reason = ''
  try {
    const entered = await $.prompt.submit({ text: request.text, asUser: true })
    if (entered.drop !== undefined) reason = entered.drop || 'Claude Code refused the request.'
  } catch (failure) {
    reason = failure instanceof Error ? failure.message : String(failure)
  }
  if (reason) await post($, server, '/internal/request_failed', { id: request.id, reason })
}

async function openWindow($: EngineInterface): Promise<string> {
  const server = await startServer($)
  const { value } = await post($, server, '/internal/open', {})
  $.ui.status(`math-worksheet: ${server.url.split('?')[0]}`)
  const opened = value.opened
  if (!opened) return `Couldn't start a browser. Open this address yourself: ${server.url}`

  return `Opened the worksheet in ${String(opened)}: ${server.url}`
}

function toolArguments(e: Record<string, unknown>): Record<string, unknown> {
  const { tool: _tool, tool_use_id: _id, ...rest } = e

  return rest
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'worksheet',
      description: 'Open your worksheets, study guides and equation sheets beside the terminal',
    })
    for (const tool of TOOLS) await $.tool.register(tool)

    return next(e)
  })

  on('command.run', { command: 'worksheet' }, async $ => {
    try {
      const opened = await openWindow($)

      return {
        text: opened,
        context: [
          'To make a new worksheet from a textbook and sections, follow the make-set skill. For a study guide or equation sheet, follow the make-doc skill. To index library files, follow the make-index skill. Never commit or push with git for the student.',
        ],
      }
    } catch (failure) {
      return { text: `Couldn't start the worksheet: ${failure instanceof Error ? failure.message : String(failure)}` }
    }
  })

  for (const tool of TOOLS) {
    if (tool.name === 'open_worksheet') continue
    on('tool.call', { tool: `mcp__${PLUGIN}__${tool.name}` }, async ($, e) => {
      try {
        const server = await startServer($)
        const { ok, value } = await post($, server, '/internal/tool', {
          name: tool.name,
          args: toolArguments(e as unknown as Record<string, unknown>),
        })
        if (!ok) return { deny: String(value.error ?? 'the worksheet server refused that') }

        return { result: JSON.stringify(value, null, 1) }
      } catch (failure) {
        return { deny: failure instanceof Error ? failure.message : String(failure) }
      }
    })
  }

  on('tool.call', { tool: `mcp__${PLUGIN}__open_worksheet` }, async $ => {
    try {
      return { result: await openWindow($) }
    } catch (failure) {
      return { deny: failure instanceof Error ? failure.message : String(failure) }
    }
  })
}
