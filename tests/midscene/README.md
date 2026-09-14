# Midscene experimental branch

Everything under this directory is developed on `ci/midscene-e2e`; it is not
part of the release branch.

The first CI stage proves that a GitHub-hosted runner can install the exact
official Omarchy ISO in a headless QEMU/KVM VM. It reuses Omarchy's own ISO
acceptance harness and saves installer screenshots and logs as short-lived
artifacts. It does not upload the VM disk or its temporary SSH key.

After that probe is stable, the next stages are:

1. Boot a throwaway overlay from the installed base image.
2. install and enable this checkout as `md.lifeos.doubao-say` in the guest.
3. Run deterministic GTK UI fixtures with `@midscene/computer` on ordinary
   Ubuntu for every trusted change.
4. Run a smaller Omarchy/Hyprland Midscene suite manually in the guest.

The AI stage needs a multimodal model credential. No local credential is
copied into GitHub automatically, and tests must contain only synthetic data.
