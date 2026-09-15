#!/usr/bin/env node

import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const MANIFEST_VERSION = 1;

function parseArguments(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value === undefined) {
      throw new Error(`Invalid argument near ${key ?? '<end>'}`);
    }
    options[key.slice(2)] = value;
  }
  return options;
}

function required(options, name) {
  const value = options[name];
  if (!value) {
    throw new Error(`Missing --${name}`);
  }
  return value;
}

function validateRunId(value) {
  if (!/^\d+$/.test(value)) {
    throw new Error('Run ID must contain digits only');
  }
  return value;
}

function validateRetention(value) {
  const retention = Number(value);
  if (!Number.isInteger(retention) || retention < 1 || retention > 50) {
    throw new Error('Report retention must be an integer from 1 to 50');
  }
  return retention;
}

function normalizeBaseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new Error('Pages URL must use HTTP or HTTPS');
  }
  if (!url.pathname.endsWith('/')) {
    url.pathname += '/';
  }
  return url;
}

async function findHtmlFiles(directory) {
  const results = [];
  async function visit(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const item = path.join(current, entry.name);
      if (entry.isDirectory()) {
        await visit(item);
      } else if (entry.isFile() && entry.name.endsWith('.html')) {
        results.push(item);
      }
    }
  }
  await visit(directory);
  return results;
}

function collectModelUsage(reportHtml) {
  const calls = new Map();
  const dumps = reportHtml.matchAll(
    /<script\s+type=["']midscene_web_dump["'][^>]*>\s*(\{[\s\S]*?)<\/script>/g,
  );

  function visit(value) {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== 'object') {
      return;
    }
    const usage = value.usage;
    if (usage && typeof usage === 'object') {
      const callId = usage._midscene_call_id;
      if (typeof callId === 'string' && !calls.has(callId)) {
        calls.set(callId, {
          durationMs: Number.isFinite(usage.time_cost) ? usage.time_cost : null,
          tokens: Number.isFinite(usage.total_tokens) ? usage.total_tokens : null,
        });
      }
    }
    Object.values(value).forEach(visit);
  }

  function normalizeJsonControlCharacters(source) {
    let normalized = '';
    let insideString = false;
    let escaped = false;
    for (const character of source) {
      if (!insideString) {
        normalized += character;
        if (character === '"') insideString = true;
        continue;
      }
      if (escaped) {
        normalized += character;
        escaped = false;
      } else if (character === '\\') {
        normalized += character;
        escaped = true;
      } else if (character === '"') {
        normalized += character;
        insideString = false;
      } else if (character.charCodeAt(0) <= 0x1f) {
        normalized += `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`;
      } else {
        normalized += character;
      }
    }
    return normalized;
  }

  for (const match of dumps) {
    try {
      visit(JSON.parse(normalizeJsonControlCharacters(match[1])));
    } catch (error) {
      throw new Error(`Cannot parse embedded Midscene report data: ${error.message}`);
    }
  }

  const durations = [...calls.values()]
    .map((call) => call.durationMs)
    .filter((duration) => duration !== null);
  const tokens = [...calls.values()]
    .map((call) => call.tokens)
    .filter((tokenCount) => tokenCount !== null);
  return {
    modelCallCount: calls.size,
    averageDurationMs: durations.length
      ? Math.round(durations.reduce((sum, duration) => sum + duration, 0) / durations.length)
      : null,
    tokenUsage: tokens.length
      ? tokens.reduce((sum, tokenCount) => sum + tokenCount, 0)
      : null,
  };
}

function validateHistoryManifest(manifest) {
  if (
    !manifest ||
    manifest.version !== MANIFEST_VERSION ||
    !Array.isArray(manifest.reports)
  ) {
    throw new Error('Existing Pages manifest has an unsupported shape');
  }
  for (const report of manifest.reports) {
    if (
      typeof report.runId !== 'string' ||
      !/^\d+$/.test(report.runId) ||
      report.reportPath !== `reports/${report.runId}/index.html` ||
      typeof report.workflowUrl !== 'string'
    ) {
      throw new Error('Existing Pages manifest contains an invalid report entry');
    }
  }
  return manifest;
}

async function fetchHistory(baseUrl) {
  const manifestUrl = new URL('reports/manifest.json', baseUrl);
  const response = await fetch(manifestUrl, { redirect: 'follow' });
  if (response.status === 404) {
    return [];
  }
  if (!response.ok) {
    throw new Error(`Cannot download existing manifest: HTTP ${response.status}`);
  }
  let manifest;
  try {
    manifest = await response.json();
  } catch (error) {
    throw new Error(`Cannot parse existing manifest: ${error.message}`);
  }
  return validateHistoryManifest(manifest).reports;
}

async function restoreReport(baseUrl, siteDirectory, report) {
  const response = await fetch(new URL(report.reportPath, baseUrl), {
    redirect: 'follow',
  });
  if (!response.ok) {
    throw new Error(
      `Cannot restore report for run ${report.runId}: HTTP ${response.status}`,
    );
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.toLowerCase().includes('text/html')) {
    throw new Error(
      `Cannot restore report for run ${report.runId}: expected text/html, got ${contentType || 'no Content-Type'}`,
    );
  }
  const destination = path.join(siteDirectory, report.reportPath);
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, Buffer.from(await response.arrayBuffer()));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatDuration(durationMs) {
  if (durationMs === null) return 'Unavailable';
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function buildIndex(reports) {
  const rows = reports
    .map(
      (report) => `
          <tr>
            <td><a href="${escapeHtml(report.workflowUrl)}">${escapeHtml(report.runId)}</a></td>
            <td>${escapeHtml(report.label ?? 'Midscene E2E')}</td>
            <td>${escapeHtml(report.generatedAt)}</td>
            <td>${escapeHtml(report.successRate.toFixed(1))}%</td>
            <td>${escapeHtml(formatDuration(report.averageDurationMs))}</td>
            <td>${escapeHtml(report.modelCallCount)}</td>
            <td>${report.tokenUsage === null ? 'Unavailable' : escapeHtml(report.tokenUsage.toLocaleString('en-US'))}</td>
            <td><a href="${escapeHtml(report.workflowUrl)}">Actions run</a></td>
            <td><a href="${escapeHtml(report.reportPath)}">HTML report</a></td>
          </tr>`,
    )
    .join('');

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Doubao Say test report history</title>
    <style>
      :root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
      body { margin: 0 auto; max-width: 1180px; padding: 2rem 1rem; }
      h1 { margin-bottom: .4rem; }
      p { color: #777; margin-top: 0; }
      .table-wrap { overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #8885; padding: .75rem; text-align: left; white-space: nowrap; }
      th { font-size: .82rem; text-transform: uppercase; }
      a { color: #2878d0; }
    </style>
  </head>
  <body>
    <main>
      <h1>Doubao Say test report history</h1>
      <p>Successful Midscene CI reports, newest first.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Run ID</th><th>Distribution</th><th>Generated (UTC)</th><th>Success rate</th><th>Avg. model call</th><th>Model calls</th><th>Tokens</th><th>Workflow</th><th>Report</th></tr></thead>
          <tbody>${rows}
          </tbody>
        </table>
      </div>
    </main>
  </body>
</html>
`;
}

export async function buildPagesReport(options) {
  const reportDirectory = path.resolve(required(options, 'report-dir'));
  const siteDirectory = path.resolve(required(options, 'site-dir'));
  const runId = validateRunId(required(options, 'run-id'));
  const workflowUrl = required(options, 'workflow-url');
  const generatedAt = new Date(required(options, 'generated-at')).toISOString();
  const retention = validateRetention(required(options, 'retention'));
  const baseUrl = normalizeBaseUrl(required(options, 'pages-url'));
  const label = options.label || 'Midscene E2E';

  const existingItems = await readdir(siteDirectory).catch((error) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  if (existingItems?.length) {
    throw new Error('Site output directory must be empty');
  }

  const htmlFiles = await findHtmlFiles(reportDirectory);
  if (htmlFiles.length !== 1) {
    throw new Error(`Expected exactly one report HTML file, found ${htmlFiles.length}`);
  }
  const reportHtml = await readFile(htmlFiles[0], 'utf8');
  const usage = collectModelUsage(reportHtml);
  const current = {
    runId,
    generatedAt,
    label,
    successRate: 100,
    testCount: 1,
    ...usage,
    workflowUrl,
    reportPath: `reports/${runId}/index.html`,
  };

  const history = (await fetchHistory(baseUrl))
    .filter((report) => report.runId !== runId)
    .slice(0, retention - 1);

  await mkdir(siteDirectory, { recursive: true });
  for (const report of history) {
    await restoreReport(baseUrl, siteDirectory, report);
  }

  const currentDestination = path.join(siteDirectory, current.reportPath);
  await mkdir(path.dirname(currentDestination), { recursive: true });
  await copyFile(htmlFiles[0], currentDestination);

  const reports = [current, ...history];
  const manifest = {
    version: MANIFEST_VERSION,
    generatedAt,
    retention,
    reports,
  };
  await mkdir(path.join(siteDirectory, 'reports'), { recursive: true });
  await writeFile(
    path.join(siteDirectory, 'reports', 'manifest.json'),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
  await writeFile(path.join(siteDirectory, 'index.html'), buildIndex(reports));
  return manifest;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const manifest = await buildPagesReport(options);
  process.stdout.write(
    `Prepared ${manifest.reports.length} report(s); newest run is ${manifest.reports[0].runId}.\n`,
  );
}

if (import.meta.url === new URL(process.argv[1], 'file:').href) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}
