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
        'Verify the Omarchy desktop visibly contains the Doubao Say window on the Sign in step, says the user is not signed in, and shows an Open Doubao sign-in button. Do not click anything.',
      );
      await agent.aiAct(
        'In the Doubao Say window, click Open Doubao sign-in exactly once, then stop immediately.',
      );
      await agent.aiAct(
        'Verify a Synthetic Doubao sign-in window is visible and explicitly says it is CI-only, makes no network request, and uses no real credentials. Do not click anything.',
      );
      await agent.aiAct(
        'In the Synthetic Doubao sign-in window, click Simulate successful sign-in exactly once, then stop immediately.',
      );
      await agent.aiAct(
        'Verify the synthetic sign-in window closed and the Doubao Say window automatically advanced to the Microphone step with a Next button in its fixed top navigation.',
      );
      await agent.aiAct(
        'Click Next in the Doubao Say fixed top navigation exactly once, then stop immediately. ' +
          'Do not click Next a second time and do not wait for or verify the page transition.',
      );
      await agent.aiAct(
        'Verify the current Doubao Say step is Trigger key. Do not click Previous or Next.',
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
        'Click Next in the fixed top navigation exactly once, then stop immediately. ' +
          'Do not click Next a second time and do not wait for or verify the page transition.',
      );
      await agent.aiAct(
        'Verify the current Doubao Say step is Voice test. Do not click Previous or Next.',
      );
      await agent.aiAct(
        'Verify the Voice test heading and introductory text are visible near the top of the content, proving that navigation reset the previous scroll position.',
      );
      await agent.aiAct(
        'Scroll within the current Voice test content if needed and click Finish setup exactly once. ' +
          'The click is successful when that same button changes to the disabled status ' +
          'Setup completed by the synthetic E2E fixture. When that status appears, stop immediately, ' +
          'do not search for Finish setup again, and do not click Previous or Next.',
      );
      await agent.aiAct(
        'Verify the Finish setup button changed to a disabled visible status saying Setup completed by the synthetic E2E fixture.',
      );
    });
  },
);
