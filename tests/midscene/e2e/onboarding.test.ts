import { spawn, type ChildProcess } from 'node:child_process';
import { resolve } from 'node:path';
import { afterAll, beforeAll, describe, it } from '@rstest/core';
import {
  type ComputerAgent,
  agentFromComputer,
} from '@midscene/computer';
import { runOnboardingFlow } from './onboarding-flow';

const sleep = (milliseconds: number) =>
  new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));

const stopProcessGroup = (child?: ChildProcess) => {
  if (!child?.pid) return;
  try {
    process.kill(-child.pid, 'SIGTERM');
  } catch {
    // The fixture may already have exited after a test failure.
  }
};

const waitForFixture = (child: ChildProcess) =>
  new Promise<void>((resolvePromise, rejectPromise) => {
    let diagnostics = '';
    const timeout = setTimeout(() => {
      rejectPromise(
        new Error(`GTK fixture did not become ready:\n${diagnostics}`),
      );
    }, 15_000);
    const capture = (chunk: Buffer) => {
      diagnostics += chunk.toString();
      if (diagnostics.includes('READY: synthetic Doubao Say GTK fixture')) {
        clearTimeout(timeout);
        resolvePromise();
      }
    };
    child.stdout?.on('data', capture);
    child.stderr?.on('data', capture);
    child.once('exit', (code, signal) => {
      clearTimeout(timeout);
      rejectPromise(
        new Error(
          `GTK fixture exited before ready (${code ?? signal}):\n${diagnostics}`,
        ),
      );
    });
  });

describe('Doubao Say onboarding', () => {
  let agent: ComputerAgent;
  let fluxbox: ChildProcess;
  let fixture: ChildProcess;

  beforeAll(async () => {
    agent = await agentFromComputer({
      aiContexts: {
        aiAct:
          'You are testing the English Doubao Say GTK onboarding window. ' +
          'Interact only with the Doubao Say window and use visible labels.',
      },
      xvfbResolution: '1280x960x24',
    });

    fluxbox = spawn('fluxbox', [], {
      detached: true,
      stdio: 'ignore',
      env: process.env,
    });
    await sleep(1000);

    const repositoryRoot = resolve(import.meta.dirname, '../../..');
    fixture = spawn('/usr/bin/python3', ['tests/midscene/gtk_fixture.py'], {
      cwd: repositoryRoot,
      detached: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        GDK_BACKEND: 'x11',
        GSK_RENDERER: 'cairo',
        GTK_A11Y: 'none',
        PYTHONPATH: resolve(repositoryRoot, 'src'),
        XDG_CONFIG_HOME: resolve(repositoryRoot, '.midscene-config'),
      },
    });
    await waitForFixture(fixture);
    await sleep(1000);
  });

  afterAll(() => {
    stopProcessGroup(fixture);
    stopProcessGroup(fluxbox);
  });

  it('navigates, reports endpoint feedback, resets scroll and completes setup', async () => {
    await runOnboardingFlow(agent);
  });
});
