import { spawn, type ChildProcess } from 'node:child_process';
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
      await runOnboardingFlow(agent);
    });
  },
);
