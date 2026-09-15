import { spawn, type ChildProcess } from 'node:child_process';
import { afterAll, beforeAll, describe, it, vi } from 'vitest';
import {
  type ComputerAgent,
  agentFromComputer,
} from '@midscene/computer';

vi.setConfig({ testTimeout: 8 * 60 * 1000, hookTimeout: 2 * 60 * 1000 });

const sleep = (milliseconds: number) =>
  new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));

const stopProcessGroup = (child?: ChildProcess) => {
  if (!child?.pid) return;
  try {
    process.kill(-child.pid, 'SIGTERM');
  } catch {
    // The viewer may already have exited after a test failure.
  }
};

describe.skipIf(process.env.OMARCHY_E2E !== 'true')(
  'Doubao Say onboarding on Omarchy',
  () => {
    let agent: ComputerAgent;
    let fluxbox: ChildProcess;
    let viewer: ChildProcess;

    beforeAll(async () => {
      agent = await agentFromComputer({
        aiContexts: {
          aiAct:
            'You are testing the English Doubao Say GTK onboarding window ' +
            'inside a real Omarchy Hyprland virtual machine shown through a VNC viewer. ' +
            'Interact only with the Doubao Say window and use visible labels.',
        },
        // Match QEMU's 1280x800 guest framebuffer exactly. A taller Xvfb
        // makes TigerVNC letterbox the guest and offsets Midscene clicks.
        xvfbResolution: '1280x800x24',
      });

      fluxbox = spawn('fluxbox', [], {
        detached: true,
        stdio: 'ignore',
        env: process.env,
      });
      await sleep(1000);

      viewer = spawn(
        'vncviewer',
        ['-FullScreen=1', '-RemoteResize=0', '-ViewOnly=0', '127.0.0.1:5905'],
        {
          detached: true,
          stdio: 'ignore',
          env: process.env,
        },
      );
      await sleep(4000);
    });

    afterAll(() => {
      stopProcessGroup(viewer);
      stopProcessGroup(fluxbox);
    });

    it('runs the onboarding flow in the real Omarchy desktop session', async () => {
      await agent.aiAct(
        'Verify the Omarchy desktop visibly contains the Doubao Say window on the Microphone step with a Next button in its fixed top navigation.',
      );
      await agent.aiAct(
        'Click Next in the Doubao Say fixed top navigation and wait until the current step is Trigger key.',
      );
      await agent.aiAct(
        'Stay on the Trigger key step and do not click Previous or Next. Scroll down inside the light-gray central content panel until the Voice polishing heading and its controls are visible.',
      );
      await agent.aiAct(
        'On the current Trigger key step, click Test endpoint in the visible Voice polishing section and wait for the endpoint result.',
      );
      await agent.aiAct(
        'Verify a visible message says the endpoint works and includes Synthetic endpoint response.',
      );
      await agent.aiAct(
        'Click Next in the fixed top navigation and wait for the Voice test step.',
      );
      await agent.aiAct(
        'Verify the Voice test heading and introductory text are visible near the top of the content, proving that navigation reset the previous scroll position.',
      );
      await agent.aiAct(
        'Scroll within the content if needed and click Finish setup.',
      );
      await agent.aiAct(
        'Verify the Finish setup button changed to a disabled visible status saying Setup completed by the synthetic E2E fixture.',
      );
    });
  },
);
