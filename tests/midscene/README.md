# Midscene experimental branch

Everything under this directory is developed on `ci/midscene-e2e`; it is not
part of the release branch.

The first CI stage proves that a GitHub-hosted runner can install the exact
official Omarchy ISO in a headless QEMU/KVM VM. It reuses Omarchy's own ISO
acceptance harness and saves installer screenshots and logs as short-lived
artifacts. It does not upload the VM disk or its temporary SSH key.

The full Omarchy install is intentionally not triggered by ordinary pushes.
During development on this branch, deliberately add/update
`tests/midscene/.rebuild-omarchy-vm` only when the pinned ISO or installer
harness changes. Once this workflow exists on the default branch, it can also
be started with `workflow_dispatch`. GitHub-hosted runners are ephemeral, so
every such full rebuild downloads the pinned ISO again; routine Ubuntu
Midscene tests do not need the ISO.

Omarchy 4.0.3 was installed successfully on a GitHub-hosted runner in
[Actions run 34866563731](https://github.com/quanru/doubao-say/actions/runs/34866563731).
The run took 13 minutes 41 seconds, including about 55 seconds to transfer the
ISO.

After that probe is stable, the next stages are:

1. Boot a throwaway overlay from the installed base image.
2. install and enable this checkout as `md.lifeos.doubao-say` in the guest.
3. Run deterministic GTK UI fixtures with `@midscene/computer` on ordinary
   Ubuntu for every trusted change.
4. Run a smaller Omarchy/Hyprland Midscene suite manually in the guest.

The ordinary Ubuntu stage maps the real GTK onboarding window inside the
headless Midscene desktop. Its synthetic fixture performs no login, recording,
network request, paste, or user-settings read. Midscene visually navigates to
the shortcut page, runs the fake endpoint check, verifies its visible feedback,
continues to the voice page, checks that navigation reset the scroll position,
and completes setup. The HTML replay is uploaded for every run.

The AI stage needs a multimodal model credential. Its `MIDSCENE_MODEL_*`
configuration is stored only as GitHub Actions Secrets; tests contain only
synthetic data.
