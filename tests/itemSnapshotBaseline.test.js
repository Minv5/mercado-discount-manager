import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  ITEM_SNAPSHOT_BASELINE_SCOPE,
  readItemSnapshotBaselineCheckpoint,
  writeItemSnapshotBaselineCheckpoint,
} from '../src/itemSnapshotBaseline.js';

test('completed item snapshot baseline checkpoint is atomic, bounded, and reusable', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-item-baseline-'));
  const checkpoint = path.join(root, 'nested', 'baseline.json');
  try {
    assert.equal(readItemSnapshotBaselineCheckpoint(checkpoint), null);
    const written = writeItemSnapshotBaselineCheckpoint(checkpoint, {
      completed_at: '2026-09-01T03:57:33.031Z',
      relation_count: 82_767,
      refreshed_count: 31_340,
    });
    assert.equal(written.scope, ITEM_SNAPSHOT_BASELINE_SCOPE);
    assert.deepEqual(readItemSnapshotBaselineCheckpoint(checkpoint), written);
    assert.equal(fs.readdirSync(path.dirname(checkpoint)).filter((name) => name.endsWith('.tmp')).length, 0);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('wrong scope and incomplete checkpoints never suppress the one-time baseline', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mdm-item-baseline-invalid-'));
  const checkpoint = path.join(root, 'baseline.json');
  try {
    fs.writeFileSync(checkpoint, JSON.stringify({ scope: 'other', status: 'complete' }));
    assert.equal(readItemSnapshotBaselineCheckpoint(checkpoint), null);
    fs.writeFileSync(checkpoint, JSON.stringify({ scope: ITEM_SNAPSHOT_BASELINE_SCOPE, status: 'running' }));
    assert.equal(readItemSnapshotBaselineCheckpoint(checkpoint), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
