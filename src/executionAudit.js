import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

export function readAuditText(file) {
  const buffer = fs.readFileSync(file);
  return /\.gz(?:\.tmp)?$/i.test(file) ? zlib.gunzipSync(buffer).toString('utf8') : buffer.toString('utf8');
}

export function readAuditEvents(file, limit = Number.MAX_SAFE_INTEGER) {
  const lines = readAuditText(file).split(/\r?\n/).filter(Boolean);
  const safeLimit = Math.max(1, Math.min(lines.length || 1, Math.floor(Number(limit) || lines.length || 1)));
  return lines.slice(-safeLimit).map((line) => {
    try { return JSON.parse(line); }
    catch { return { parse_error: true, raw: line.slice(0, 500) }; }
  });
}

function loadState(directory, id) {
  if (!directory || !id) return null;
  const file = path.join(directory, `${String(id).replace(/[^A-Za-z0-9_.-]/g, '')}.json`);
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { return null; }
}

function auditIdentity(file) {
  const event = readAuditEvents(file, 1)[0] || {};
  if (event.parse_error) return { parse_error: true };
  return {
    job_id: String(event.jobId || event.job_id || ''),
    group_id: String(event.group_id || event.execution_group_id || ''),
  };
}

function byteCount(file) {
  return Number(fs.statSync(file).size || 0);
}

export function compressExecutionAudits({
  eventDir,
  jobStateDir,
  groupStateDir,
  olderThanMs = 30 * 24 * 60 * 60 * 1000,
  now = Date.now(),
  confirm = false,
} = {}) {
  const result = { mode: confirm ? 'confirm' : 'preview', candidates: [], blocked: [], compressed: [] };
  if (!eventDir || !fs.existsSync(eventDir)) return result;
  for (const entry of fs.readdirSync(eventDir, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith('.jsonl')) continue;
    const file = path.join(eventDir, entry.name);
    const ageMs = Number(now) - fs.statSync(file).mtimeMs;
    if (ageMs < Number(olderThanMs || 0)) continue;
    const identity = auditIdentity(file);
    const job = loadState(jobStateDir, identity.job_id);
    const group = identity.group_id ? loadState(groupStateDir, identity.group_id) : null;
    const jobTerminal = TERMINAL.has(String(job?.status || ''));
    const groupTerminal = !identity.group_id || TERMINAL.has(String(group?.status || ''));
    if (identity.parse_error || !jobTerminal || !groupTerminal) {
      result.blocked.push({ file, ...identity, reason: identity.parse_error ? 'audit_unreadable' : 'state_not_terminal' });
      continue;
    }
    const text = readAuditText(file);
    const candidate = {
      file,
      gzip_file: `${file}.gz`,
      ...identity,
      bytes: byteCount(file),
      event_count: text.split(/\r?\n/).filter(Boolean).length,
    };
    result.candidates.push(candidate);
    if (!confirm) continue;
    const temporary = `${candidate.gzip_file}.tmp`;
    fs.writeFileSync(temporary, zlib.gzipSync(Buffer.from(text, 'utf8')));
    const verified = readAuditText(temporary);
    const verifiedEvents = verified.split(/\r?\n/).filter(Boolean).length;
    if (verified !== text || verifiedEvents !== candidate.event_count) {
      fs.rmSync(temporary, { force: true });
      throw new Error(`execution audit gzip verification failed: ${entry.name}`);
    }
    if (fs.existsSync(candidate.gzip_file)) fs.rmSync(candidate.gzip_file, { force: true });
    fs.renameSync(temporary, candidate.gzip_file);
    fs.rmSync(file);
    result.compressed.push({ ...candidate, gzip_bytes: byteCount(candidate.gzip_file) });
  }
  return result;
}
