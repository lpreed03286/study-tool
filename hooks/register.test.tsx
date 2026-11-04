import { expect, test } from 'claude-code/testing'

declare const setTimeout: (done: (value: unknown) => void, ms: number) => void

const TOOLS = ['check_coverage', 'add_method', 'record_coverage', 'study_plan', 'error_log', 'create_set', 'create_doc', 'library_overview', 'library_search', 'library_read', 'library_pending', 'library_save_index', 'open_worksheet']

test('a session registers /worksheet and every tool, and starts no server by itself', async ($, on) => {
  const commands: string[] = []
  const tools: string[] = []
  let spawned = 0
  on('command.register', (_, e) => {
    commands.push(e.name)

    return { value: { command: e.name } }
  })
  on('tool.register', (_, e) => {
    tools.push(e.name)

    return { value: { tool: `mcp__math-worksheet__${e.name}` } }
  })
  on('process.spawn', async function* () {
    spawned += 1
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })

  expect(commands).toEqual(['worksheet'])
  expect(tools).toEqual(TOOLS)
  expect(spawned).toBe(0)
})

test('a request from the page becomes a prompt, and a refused one is reported back', async ($, on) => {
  const submitted: string[] = []
  const reported: unknown[] = []
  let refuse = false
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('tool.register', (_, e) => ({ value: { tool: `mcp__math-worksheet__${e.name}` } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('env.get', () => ({ value: '/home/student' }))
  on('fs.stat', () => ({ value: { isFile: true } }) as never)
  on('ui.status', () => ({ value: undefined }) as never)
  on('clock.after', () => new Promise(() => {}) as never)
  on('prompt.submit', (_, e) => {
    submitted.push(e.text)

    return refuse ? { drop: 'A hook refused it.' } : { text: e.text }
  })
  on('process.spawn', async function* () {
    yield { stream: 'stdout', text: '{"event":"ready","port":1,"token":"t","url":"http://127.0.0.1:1/?token=t"}\n' }
    yield { stream: 'stdout', text: '{"event":"prompt","id":"one","text":"make a set"}\n' }
    await new Promise(done => setTimeout(done, 5))
    refuse = true
    yield { stream: 'stdout', text: '{"event":"prompt","id":"two","text":"make another"}\n' }
    await new Promise(done => setTimeout(done, 5))

    return { value: { code: 0, signal: null } }
  } as never)
  on('http.fetch', (_, e) => {
    if (e.url.endsWith('/internal/request_failed')) reported.push(JSON.parse(String(e.init?.body)))

    return { value: { ok: true, status: 200, text: '{"opened":"librewolf"}' } } as never
  })

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const opened = await $.command.run({ command: 'worksheet', args: '' } as never)
  await new Promise(done => setTimeout(done, 30))

  expect(JSON.stringify(opened)).toContain('Opened the worksheet')
  expect(submitted).toEqual(['make a set', 'make another'])
  expect(reported).toEqual([{ id: 'two', reason: 'A hook refused it.' }])
})

function setUp(on: any, nixWorks: boolean, ran: string[][], files: Set<string>) {
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('tool.register', (_: unknown, e: { name: string }) => ({ value: { tool: `mcp__math-worksheet__${e.name}` } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('env.get', (_: unknown, e: { name?: string }) => ({ value: '/home/student' }))
  on('ui.status', () => ({ value: undefined }))
  on('clock.after', () => new Promise(() => {}))
  on('fs.stat', (_: unknown, e: { path: string }) => {
    if (files.has(e.path)) return { value: { isFile: true } }
    return { deny: 'missing' }
  })
  on('process.run', (_: unknown, e: { argv: string[] }) => {
    ran.push([...e.argv])
    const [program] = e.argv
    if (program === 'nix' && !nixWorks) return { deny: 'not found' }
    if (e.argv.includes('venv')) files.add('/home/student/.local/share/math-worksheet/python-env/bin/python')
    if (e.argv.includes('pip')) files.add('/home/student/.local/share/math-worksheet/python-env/bin/python3')
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  on('process.spawn', async function* (_: unknown, e: { argv: string[] }) {
    ran.push([...e.argv])
    yield { stream: 'stdout', text: '{"event":"ready","port":1,"token":"t","url":"http://127.0.0.1:1/?token=t"}\n' }
    await new Promise(done => setTimeout(done, 5))

    return { value: { code: 0, signal: null } }
  })
  on('http.fetch', () => ({ value: { ok: true, status: 200, text: '{"opened":"your default browser"}' } }))
}

test('someone without Nix gets a virtual environment with the packages, in a neutral folder', async ($, on) => {
  const ran: string[][] = []
  setUp(on, false, ran, new Set())
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const opened = await $.command.run({ command: 'worksheet', args: '' } as never)
  await new Promise(done => setTimeout(done, 20))

  expect(JSON.stringify(opened)).toContain('Opened the worksheet')
  const env = '/home/student/.local/share/math-worksheet/python-env'
  expect(ran.some(c => c.join(' ') === `python3 -m venv ${env}`)).toBe(true)
  expect(ran.some(c => c[0] === `${env}/bin/python` && c.includes('sympy==1.14.0'))).toBe(true)
  expect(ran.some(c => c[0] === `${env}/bin/python3` && c.some(a => a.endsWith('server/server.py')))).toBe(true)
})

test('an existing install keeps its data folder and builds nothing', async ($, on) => {
  const ran: string[][] = []
  const env = '/home/student/.local/share/mae692-worksheets'
  setUp(on, true, ran, new Set([env, `${env}/python-env/bin/python3`]))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'worksheet', args: '' } as never)
  await new Promise(done => setTimeout(done, 20))

  expect(ran.some(c => c[0] === 'nix' || c.includes('venv'))).toBe(false)
  expect(ran.some(c => c[0] === `${env}/python-env/bin/python3`)).toBe(true)
})
