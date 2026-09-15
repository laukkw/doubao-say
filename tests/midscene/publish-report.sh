#!/usr/bin/env bash
set -euo pipefail

readonly DISTRO_SLUG="${1:?distribution slug is required}"
readonly ARTIFACT_DIR="${2:?artifact directory is required}"

case "$DISTRO_SLUG" in
  ubuntu-22.04|omarchy-4.0.3) ;;
  *)
    echo "Unsupported distribution slug: $DISTRO_SLUG" >&2
    exit 1
    ;;
esac

if [[ -z "${PAGES_TOKEN:-}" || -z "${GITHUB_REPOSITORY:-}" ||
      -z "${GITHUB_RUN_ID:-}" ]]; then
  echo "GitHub Pages publishing environment is incomplete" >&2
  exit 1
fi

report="$(find "$ARTIFACT_DIR" -type f -name '*.html' -print -quit 2>/dev/null || true)"
if [[ -z "$report" ]]; then
  echo "### Midscene HTML report" >> "$GITHUB_STEP_SUMMARY"
  echo "No HTML report was generated for this run." >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi

site_dir="$(mktemp -d)"
trap 'rm -rf -- "$site_dir"' EXIT
remote="https://x-access-token:${PAGES_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

if ! git clone --quiet --depth 1 --branch gh-pages "$remote" "$site_dir"; then
  rm -rf -- "$site_dir"
  mkdir -p "$site_dir"
  git -C "$site_dir" init --quiet --initial-branch=gh-pages
  git -C "$site_dir" remote add origin "$remote"
fi

runs_dir="$site_dir/midscene/$DISTRO_SLUG/runs"
run_dir="$runs_dir/$GITHUB_RUN_ID"
mkdir -p "$run_dir"
cp "$report" "$run_dir/index.html"
cp "$report" "$site_dir/midscene/$DISTRO_SLUG/index.html"
touch "$site_dir/.nojekyll"

# Keep exact links for the five newest runs of each distribution. The stable
# distribution URL above always points to its latest report.
while IFS= read -r old_run; do
  [[ "$old_run" =~ ^[0-9]+$ ]] || continue
  rm -rf -- "$runs_dir/$old_run"
done < <(find "$runs_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' |
  sort -nr | tail -n +6)

cat > "$site_dir/index.html" <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Doubao Say · Midscene E2E reports</title>
  </head>
  <body>
    <main>
      <h1>Doubao Say · Midscene E2E reports</h1>
      <p>These synthetic test reports and their screenshots are public.</p>
      <ul>
        <li><a href="midscene/ubuntu-22.04/">Ubuntu 22.04 · latest</a></li>
        <li><a href="midscene/omarchy-4.0.3/">Omarchy 4.0.3 · latest</a></li>
      </ul>
    </main>
  </body>
</html>
EOF

git -C "$site_dir" config user.name "github-actions[bot]"
git -C "$site_dir" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git -C "$site_dir" add --all
git -C "$site_dir" commit --quiet -m "docs: publish $DISTRO_SLUG report for run $GITHUB_RUN_ID"
git -C "$site_dir" push --quiet origin gh-pages

repository_name="${GITHUB_REPOSITORY#*/}"
report_url="https://${GITHUB_REPOSITORY_OWNER}.github.io/${repository_name}/midscene/${DISTRO_SLUG}/runs/${GITHUB_RUN_ID}/"
{
  echo "### Midscene HTML report"
  echo "[Open the interactive ${DISTRO_SLUG} report](${report_url})"
  echo
  echo "The report and its screenshots are publicly accessible through GitHub Pages."
} >> "$GITHUB_STEP_SUMMARY"
